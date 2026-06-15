# Duplicate-contact / identity matching.
#
# Problem: one real human reaches the store on many channels — Instagram DM,
# email, WhatsApp — and Chatwoot files each as a SEPARATE contact. The agent
# helping them on Instagram has no idea they emailed yesterday about the same
# order. This module finds "this is probably the same person as contact X"
# candidates and scores how sure we are, so an agent can merge them.
#
# ── Design: a TWO-GATE scorer (why it's safe) ─────────────────────────────
# The naive version ("they mentioned the same order id → same person") is
# WRONG: a brother buys a gift (Order #4521), his sister receives it, both
# message support about #4521 — different people, same order. So:
#
#   GATE 1 (must pass to score at all): at least one PERSONAL identifier
#   matches — email, phone, or a fuzzy full-name match. Order ids, product
#   names and timing are NOT personal identifiers and can never open the
#   gate. Two different people sharing an order never get suggested, because
#   their emails/phones/names differ → Gate 1 stays shut.
#
#   GATE 2 (boosters, only after Gate 1 opened): corroborating signals that
#   raise an ALREADY-personal match's confidence. (Order-id corroboration is
#   a planned booster — see note in score_pair — deliberately booster-only so
#   it can never start a match.)
#
# The bridge NEVER auto-merges. It surfaces candidates + the evidence behind
# the score; a human clicks merge (Chatwoot's merge deletes the absorbed
# contact — partially irreversible, so it stays a human call).
#
# Pure-Python + stdlib only (difflib for fuzzy names) — no LLM, no new deps.
# score_pair() is side-effect-free and unit-testable; find_matches() does I/O.

import asyncio
import difflib
import re

import config
import chatwoot

# ── Confidence bands (score 0-100 → label) ────────────────────────────────
# Thresholds line up with config.IDENTITY_MATCH_MIN_SCORE (surface floor) and
# IDENTITY_MATCH_NOTE_SCORE (inline-note floor).
BAND_NONE = "none"
BAND_POSSIBLE = "possible"        # 31-60  → faint sidebar suggestion
BAND_LIKELY = "likely"            # 61-85  → prominent sidebar card
BAND_NEAR_CERTAIN = "near_certain"  # 86-100 → also a proactive private note


def band_for(score: int) -> str:
    if score >= 86:
        return BAND_NEAR_CERTAIN
    if score >= 61:
        return BAND_LIKELY
    if score >= 31:
        return BAND_POSSIBLE
    return BAND_NONE


# ── Scoring weights ───────────────────────────────────────────────────────
# Calibrated against the bands so each combination lands sensibly:
#   email only ........... 80  → likely      (globally-unique, stable id)
#   phone only ........... 45  → possible     (capped: families share numbers)
#   name exact only ...... 40  → possible     (common names collide)
#   name fuzzy only ...... 31-39 → possible    (weakest signal)
#   email + phone ........ 100 → near-certain
#   email + name ......... 100 → near-certain
#   phone + name exact ... 85  → likely        (never near-certain w/o email)
# near-certain (>=86) is therefore reachable ONLY with an email match, which
# is enforced explicitly too (strong_id guard in score_pair).
W_EMAIL = 80
W_PHONE = 45
W_NAME_EXACT = 40      # normalised names identical / near-identical
W_NAME_FUZZY_MIN = 31  # floor of the fuzzy-name band (just clears MIN_SCORE)
W_NAME_FUZZY_MAX = 39  # ceiling of the fuzzy-name band (just under exact)

# Fuzzy full-name similarity (0..1) thresholds. See name_ratio() — the metric
# is a per-token best-match MIN, so a shared surname alone scores LOW ("Rahul
# Sharma" vs "Priya Sharma" ≈ 0.68) and siblings don't match.
NAME_GATE_RATIO = 0.75      # >= this earns name points + opens the gate
NAME_EXACT_RATIO = 0.95     # >= this counts as an exact-name match
NAME_MISMATCH_RATIO = 0.40  # < this (both names present) is an explicit "differs";
                            # the 0.40-0.75 middle is ambiguous → no evidence row


# ── Normalisation helpers ─────────────────────────────────────────────────
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Phone extraction from FREE TEXT is conservative on purpose: message bodies
# are full of numbers that are NOT phones (order ids, invoice numbers,
# tracking codes). We only accept either an international +<10-15 digits> or
# an Indian-mobile-shaped 10-digit number starting 6-9 (with optional
# separators). This avoids capturing a 6-digit order number as a "phone".
_TEXT_PHONE_RE = re.compile(
    r"""(?x)
    (?<!\d)
    (
        \+\d[\d\s\-().]{8,16}\d      # +91 98765 43210 / +1 (415) 555-0132
      | (?:0|91)?[\s\-]?[6-9]\d{2}[\s\-]?\d{3}[\s\-]?\d{4}  # 98765 43210
    )
    (?!\d)
    """
)


def norm_email(raw: str) -> str:
    return (raw or "").strip().lower()


def canon_phone(raw: str) -> str | None:
    """Canonical phone = last 10 digits, or None if fewer than 10 digits.
    Used for both contact-record phones (trusted) and text-extracted ones.
    Comparing the last 10 digits collapses +91/0/country-code variations to a
    single key without a phone-parsing dependency (sufficient for IN + most
    international mobiles)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) < 10:
        return None
    return digits[-10:]


def extract_emails(text: str) -> set[str]:
    return {norm_email(m) for m in _EMAIL_RE.findall(text or "")}


def extract_phones(text: str) -> set[str]:
    out: set[str] = set()
    for m in _TEXT_PHONE_RE.findall(text or ""):
        c = canon_phone(m)
        if c:
            out.add(c)
    return out


def norm_name(raw: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Empty for the
    auto-generated placeholder names Chatwoot/Meta hand out ("Instagram
    User", "John Doe", a bare handle) — those must NEVER open the name gate,
    or every IG contact would 'match' every other one."""
    s = re.sub(r"[^a-z\s]", " ", (raw or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s in _PLACEHOLDER_NAMES:
        return ""
    return s


# Placeholder / non-identifying names that should not count as a name signal.
_PLACEHOLDER_NAMES = {
    "", "instagram user", "facebook user", "whatsapp user", "john doe",
    "jane doe", "unknown", "guest", "user", "customer", "anonymous",
    "test", "n a", "na",
}


def _token_best_avg(tokens_a: list[str], tokens_b: list[str]) -> float:
    """Average, over each token in A, of its BEST fuzzy match against any
    token in B. Per-token alignment is what lets 'kumar rahul' match 'rahul
    kumar' (reorder) and 'rahul' match 'rahuul' (typo), while NOT rewarding a
    lone shared surname too much."""
    if not tokens_a or not tokens_b:
        return 0.0
    total = 0.0
    for ta in tokens_a:
        total += max(
            difflib.SequenceMatcher(None, ta, tb).ratio() for tb in tokens_b
        )
    return total / len(tokens_a)


def name_ratio(a: str, b: str) -> float:
    """Fuzzy full-name similarity in 0..1.

    Computed as the MIN of the two directional per-token averages, so BOTH
    names must be well covered for a high score. This is the key to not
    matching on a shared surname: "Rahul Sharma" vs "Priya Sharma" — the
    first names (rahul/priya) align poorly, so each direction averages ~0.68
    and the min stays well under the 0.75 gate. Reorders and typos still
    score high because every token finds a near-twin in the other name."""
    na, nb = norm_name(a), norm_name(b)
    if not na or not nb:
        return 0.0
    ta, tb = na.split(), nb.split()
    return min(_token_best_avg(ta, tb), _token_best_avg(tb, ta))


# ── Signal containers ─────────────────────────────────────────────────────
def signals_from_contact(contact: dict, message_text: str = "") -> dict:
    """Collect identity signals for ONE side of a comparison.

    Pulls email / phone / name from the contact record, and additionally
    harvests emails / phones the customer typed into `message_text` (their
    own contact details mentioned in chat — a common way the same person is
    reachable across channels). The contact's own name is the only name
    signal; we don't try to parse names out of free text."""
    email = norm_email(contact.get("email"))
    phone = canon_phone(contact.get("phone_number") or contact.get("phone"))
    emails = {email} if email else set()
    phones = {phone} if phone else set()
    emails |= extract_emails(message_text)
    phones |= extract_phones(message_text)
    return {
        "emails": emails,
        "phones": phones,
        "name": contact.get("name") or "",
    }


# ── The scorer (pure, unit-testable) ──────────────────────────────────────
def score_pair(subject: dict, candidate: dict) -> dict | None:
    """Score how likely `subject` and `candidate` (both signal dicts from
    signals_from_contact) are the SAME person.

    Returns None if Gate 1 fails (no personal identifier matched) — the
    caller drops the candidate entirely. Otherwise returns:
        {score, band, matched: [...], mismatched: [...], note}
    where matched/mismatched are evidence rows the UI renders verbatim so the
    agent sees WHY, not just a number."""
    matched: list[dict] = []
    mismatched: list[dict] = []
    score = 0
    gate_open = False
    strong_id = False  # email matched → eligible for near-certain band

    # ── Email ──
    shared_emails = subject["emails"] & candidate["emails"]
    if shared_emails:
        score += W_EMAIL
        gate_open = True
        strong_id = True
        matched.append({
            "type": "email", "label": "Email",
            "detail": sorted(shared_emails)[0],
        })
    elif subject["emails"] and candidate["emails"]:
        mismatched.append({"type": "email", "label": "Different email"})

    # ── Phone ──
    shared_phones = subject["phones"] & candidate["phones"]
    if shared_phones:
        score += W_PHONE
        gate_open = True
        matched.append({
            "type": "phone", "label": "Phone",
            "detail": _mask_phone(sorted(shared_phones)[0]),
        })
    elif subject["phones"] and candidate["phones"]:
        mismatched.append({"type": "phone", "label": "Different phone"})

    # ── Name ──
    nr = name_ratio(subject["name"], candidate["name"])
    if nr >= NAME_EXACT_RATIO:
        score += W_NAME_EXACT
        gate_open = True
        matched.append({
            "type": "name", "label": "Name",
            "detail": (candidate["name"] or "").strip(),
        })
    elif nr >= NAME_GATE_RATIO:
        # Scale fuzzy points across the band [NAME_GATE_RATIO, NAME_EXACT_RATIO).
        span = NAME_EXACT_RATIO - NAME_GATE_RATIO
        frac = (nr - NAME_GATE_RATIO) / span if span else 0.0
        pts = round(W_NAME_FUZZY_MIN + frac * (W_NAME_FUZZY_MAX - W_NAME_FUZZY_MIN))
        score += pts
        gate_open = True
        matched.append({
            "type": "name", "label": "Similar name",
            "detail": (candidate["name"] or "").strip(),
        })
    elif (
        nr < NAME_MISMATCH_RATIO
        and norm_name(subject["name"])
        and norm_name(candidate["name"])
    ):
        # Only call it a mismatch when the names are clearly different. The
        # 0.40-0.75 middle (e.g. "R. Kumar" vs "Rahul Kumar") is ambiguous —
        # neither evidence for nor against — so we add no row either way.
        mismatched.append({"type": "name", "label": "Name differs"})

    # GATE 1: no personal identifier matched → not a candidate at all.
    if not gate_open:
        return None

    # ── GATE 2 boosters (only reached once Gate 1 is open) ──
    # Order-id / product / temporal corroboration goes HERE when added. It is
    # intentionally booster-only: it raises an existing personal match's
    # score but can never (by living below this gate) start one. That's what
    # keeps the "gift bought for someone else, same order id" case safe —
    # those two people never open Gate 1, so this code never runs for them.
    # (Deferred from v1: cheaply fetching a candidate's historical order ids
    # needs an extra messages call per candidate; the gate already delivers
    # the safety, so corroboration is a pure accuracy nicety for later.)

    score = min(score, 100)

    # Phone-only / name-only matches are the classic false-positive shapes
    # (shared family phone; common name). Cap them below near-certain and
    # attach a caution the agent sees before merging.
    note = ""
    only_phone = (
        any(m["type"] == "phone" for m in matched)
        and not strong_id
        and not any(m["type"] == "name" for m in matched)
    )
    only_name = (
        any(m["type"] == "name" for m in matched)
        and not strong_id
        and not any(m["type"] == "phone" for m in matched)
    )
    if not strong_id and score >= 86:
        score = 85  # never reach near-certain without an email match
    if only_phone:
        note = "Phone match only — could be a shared family number. Verify before merging."
    elif only_name:
        note = "Name match only — verify this is the same person before merging."

    return {
        "score": score,
        "band": band_for(score),
        "matched": matched,
        "mismatched": mismatched,
        "note": note,
    }


def _mask_phone(canon: str) -> str:
    """Show only the last 4 digits in evidence — enough for the agent to
    recognise the number without splaying full PII into the sidebar."""
    if not canon or len(canon) < 4:
        return "••••"
    return "••••••" + canon[-4:]


# ── Orchestration (does the I/O) ──────────────────────────────────────────
async def find_matches(
    contact: dict,
    message_text: str = "",
    exclude_contact_id=None,
) -> list[dict]:
    """Find duplicate-contact candidates for `contact`.

    1. Build the subject's signals (contact record + identifiers typed in the
       message).
    2. Search Chatwoot contacts by each identifier (email, phone, name) and
       union the candidates.
    3. Score each with score_pair(); drop Gate-1 failures and anything below
       config.IDENTITY_MATCH_MIN_SCORE.
    4. Return up to IDENTITY_MATCH_MAX_CANDIDATES, highest score first.

    Best-effort: returns [] on any failure (identity matching never blocks a
    webhook)."""
    subject = signals_from_contact(contact, message_text)

    # Nothing to search on → nothing to find.
    queries: list[str] = []
    queries += sorted(subject["emails"])
    queries += sorted(subject["phones"])
    if norm_name(subject["name"]):
        queries.append(subject["name"].strip())
    if not queries:
        return []

    # Gather + de-dupe candidates across all searches. Queries run in
    # parallel (asyncio.gather): each /contacts/search is an independent
    # round-trip and we'd otherwise wait sequentially for up to three of
    # them. search_contacts already swallows exceptions internally, so
    # gather will never raise here.
    search_results = await asyncio.gather(
        *(chatwoot.search_contacts(q) for q in queries)
    )
    candidates: dict = {}
    for results_list in search_results:
        for c in results_list:
            cid = c.get("id")
            if cid is None:
                continue
            if exclude_contact_id is not None and str(cid) == str(exclude_contact_id):
                continue
            candidates[cid] = c

    results: list[dict] = []
    for cid, cand in candidates.items():
        cand_sig = signals_from_contact(cand)
        scored = score_pair(subject, cand_sig)
        if not scored or scored["score"] < config.IDENTITY_MATCH_MIN_SCORE:
            continue
        results.append({
            "contact_id": cid,
            "name": (cand.get("name") or "").strip(),
            "email": cand.get("email") or "",
            "phone": cand.get("phone_number") or cand.get("phone") or "",
            "score": scored["score"],
            "band": scored["band"],
            "matched": scored["matched"],
            "mismatched": scored["mismatched"],
            "note": scored["note"],
            "last_activity_at": cand.get("last_activity_at"),
        })

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[: config.IDENTITY_MATCH_MAX_CANDIDATES]
