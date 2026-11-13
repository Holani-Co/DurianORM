# info@durian.in routing — the general mailbox (Chatwoot inbox "Durian Info").
#
# Separate from the hello@ email pipeline: its own category list and owners
# (info_routing_rules.yaml), so hello@ routing is untouched. For each new email
# thread, main._handle_info_inbox:
#   1. decide(): a sender rule (no AI) for ERP copies, internal @durian.in mail,
#      account alerts and bounces — otherwise the AI picks one info@ category;
#   2. route():  forward  → owner set and the AI is confident: email the trail
#                           to the owner, tag, resolve;
#                hold     → no owner set yet: label + note, left in the inbox;
#                review   → AI not sure: label + note, left for a person.
# A later email in a thread we forwarded goes to the same owners
# (followup_check stops loops: internal senders, bounces, a daily cap).
#
# Everything here is pure except classify() (one LLM call), so the routing
# decisions are testable without Chatwoot.

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

import classifier
import config

_RULES_PATH = Path(os.environ.get("INFO_ROUTING_RULES_PATH")
                   or Path(__file__).with_name("info_routing_rules.yaml"))
_cache: dict = {"mtime": None, "rules": None}

FALLBACK = "fallback"
NOT_SURE_LABEL = "info-not-sure"
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_BOUNCE_LOCALS = ("mailer-daemon", "postmaster")
# Sender local parts that mark machine mail — never forwarded as a follow-up
# (a bounce of our own forward threads back into the conversation).
_AUTOMATED_TAGS = ("no-reply", "noreply", "no_reply", "donotreply", "do-not-reply",
                   "do_not_reply", "mailer-daemon", "mailerdaemon", "postmaster", "bounce")
_BODY_CAP = 6000            # chars of email body the AI reads
_FOLLOWUP_IDS_KEPT = 20


# ── Rules ────────────────────────────────────────────────────────────────────

def load_rules(force: bool = False) -> dict:
    """info_routing_rules.yaml, re-read when the file changes. Raises on a
    missing or broken file — the caller leaves the email untouched then."""
    mtime = _RULES_PATH.stat().st_mtime
    if force or _cache["rules"] is None or _cache["mtime"] != mtime:
        with open(_RULES_PATH, encoding="utf-8") as f:
            rules = yaml.safe_load(f) or {}
        for problem in validate(rules):
            print(f"[info] rules warning: {problem}")
        _cache.update(mtime=mtime, rules=rules)
    return _cache["rules"]


def categories(rules: dict) -> dict:
    """Active categories (a `disabled: true` one is hidden from the AI)."""
    return classifier._active(rules.get("categories"))


def recipients(value) -> list[str]:
    """forward_to / cc as a clean list — accepts "a@x, b@y" or a YAML list."""
    if not value:
        return []
    parts = value if isinstance(value, list) else str(value).split(",")
    out: list[str] = []
    for p in parts:
        p = str(p).strip()
        if p and p.lower() not in {o.lower() for o in out}:
            out.append(p)
    return out


def validate(rules: dict) -> list[str]:
    """Problems that would make routing misbehave (empty list = fine)."""
    problems = []
    cats = (rules or {}).get("categories") or {}
    for key, cfg in cats.items():
        cfg = cfg or {}
        action = cfg.get("action")
        if action not in ("forward", "hold"):
            problems.append(f"{key}: action must be forward or hold (got {action!r})")
        if action == "forward":
            to = recipients(cfg.get("forward_to"))
            if not to:
                problems.append(f"{key}: forward category without forward_to")
            for addr in to + recipients(cfg.get("cc")):
                if not _EMAIL_RE.match(addr):
                    problems.append(f"{key}: not an email address: {addr!r}")
    for r in (rules or {}).get("sender_rules") or []:
        if (r or {}).get("category") not in cats:
            problems.append(f"sender rule {r!r}: unknown category")
    return problems


def display_name(rules: dict, key: str) -> str:
    cfg = ((rules.get("categories") or {}).get(key) or {})
    return cfg.get("display_name") or (key or "").replace("_", " ").capitalize()


def label_for(category: str) -> str:
    """Conversation label for a category: info-govt-psu-business, …"""
    if not category or category == FALLBACK:
        return NOT_SURE_LABEL
    return "info-" + category.replace("_", "-")


# ── Sender helpers ───────────────────────────────────────────────────────────

def _split(email: str) -> tuple[str, str]:
    """(local part, domain), lowercased; ("", "") when it isn't an address."""
    email = (email or "").strip().lower()
    if "@" not in email:
        return "", ""
    local, _, domain = email.rpartition("@")
    return local, domain


def is_internal(email: str, internal_domains) -> bool:
    _, domain = _split(email)
    return bool(domain) and any(domain == d or domain.endswith("." + d)
                                for d in (internal_domains or ()))


def is_automated(email: str) -> bool:
    local, _ = _split(email)
    return bool(local) and any(tag in local for tag in _AUTOMATED_TAGS)


def sender_rule(sender_email: str, rules: dict) -> Optional[str]:
    """Category for mail we route without the AI, or None. Bounces
    (mailer-daemon / postmaster at any domain) are account alerts."""
    local, domain = _split(sender_email)
    if not domain:
        return None
    cats = categories(rules)
    if local in _BOUNCE_LOCALS and "internal_notice" in cats:
        return "internal_notice"
    email = f"{local}@{domain}"
    for r in rules.get("sender_rules") or []:
        pattern = str((r or {}).get("sender") or "").strip().lower()
        cat = (r or {}).get("category")
        if not pattern or cat not in cats:
            continue
        if pattern.startswith("@"):
            d = pattern[1:]
            if domain == d or domain.endswith("." + d):
                return cat
        elif email == pattern:
            return cat
    return None


# ── AI classification ────────────────────────────────────────────────────────

def build_prompt(rules: dict) -> str:
    """System prompt for info@ — same layout as the hello@ classifier, with an
    info@ introduction and the info@ category list."""
    cats = categories(rules)
    cap = classifier._examples_cap(rules)
    lines = [
        "You are an email-routing classifier for Durian Industries, an Indian "
        "furniture manufacturer. This is the company's general mailbox, "
        "info@durian.in. It receives government and PSU tenders and contracts, "
        "bank, tax and regulator mail, shipping documents, companies pitching "
        "products or services to Durian, newsletters and event invitations, "
        "internal copies, and the occasional customer complaint. Read the "
        "email's subject, sender and body and assign it to EXACTLY ONE of the "
        "categories below.",
        "",
        "Categories:",
        "",
    ]
    for key, cfg in cats.items():
        lines.append(f"- {key} ({cfg.get('display_name', key)})")
        for descline in (cfg.get("description") or "").strip().splitlines():
            lines.append(f"    {descline.strip()}")
        examples = cfg.get("examples") or []
        if examples:
            lines.append("  Example subject lines:")
            for ex in examples[:cap]:
                lines.append(f'    • "{ex}"')
        keywords = cfg.get("keywords") or []
        if keywords:
            shown = ", ".join(str(k) for k in keywords[:config.CATEGORY_KEYWORDS_IN_PROMPT])
            lines.append(f"  Common keywords: {shown}")
        lines.append("")
    lines.append("Rules:")
    lines.extend(f"  • {r}" for r in (rules.get("category_ai_rules") or []) if str(r).strip())
    lines.extend([
        "  • Output a confidence score 0.0-1.0 reflecting how cleanly the "
        "email matches your chosen category. Use < 0.6 when uncertain.",
        "  • Brief reason: one sentence, what signal led you to the category.",
        "  • alternatives: the 2 NEXT most likely categories (after your top "
        "pick), each with its own 0.0-1.0 confidence, most-likely first. "
        "Omit categories that clearly don't apply — fewer is fine.",
    ])
    return "\n".join(lines)


async def classify(content: str, sender_email: str = "", subject: str = "",
                   lf_parent: Optional[dict] = None) -> dict:
    """The AI's pick: {category, confidence, reason, alternatives, raw_category}.
    Never raises — any failure (or an empty email) comes back as FALLBACK."""
    rules = load_rules()
    keys = list(categories(rules).keys())
    safe = {"category": FALLBACK, "confidence": 0.0, "reason": "",
            "alternatives": [], "raw_category": None}
    if not keys or not ((content or "").strip() or (subject or "").strip()):
        return {**safe, "reason": "The email has no subject or text to read."}

    parts = []
    if subject:
        parts.append(f"Subject: {subject}")
    if sender_email:
        parts.append(f"From: {sender_email}")
    body = (content or "").strip()
    if len(body) > _BODY_CAP:
        body = body[:_BODY_CAP] + "\n[…]"
    parts.append(f"Body:\n{body or '(no text)'}")
    try:
        r = await classifier.client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0,
            max_tokens=200,
            response_format={"type": "json_schema",
                             "json_schema": classifier._category_response_schema(keys)},
            messages=[{"role": "system", "content": build_prompt(rules)},
                      {"role": "user", "content": "\n".join(parts)}],
            name="info-inbox-classification",
            metadata={"subject_preview": (subject or "")[:80],
                      "langfuse_tags": ["classifier", "info-routing"]},
            **(lf_parent or {}),
        )
        parsed = json.loads(r.choices[0].message.content or "")
    except Exception as e:  # noqa: BLE001 — classification must never break intake
        print(f"[info] classify failed ({type(e).__name__}): {e}")
        return dict(safe)

    cat = parsed.get("category") or FALLBACK
    confidence = float(parsed.get("confidence") or 0)
    alternatives = [
        {"category": a.get("category"), "confidence": float(a.get("confidence") or 0)}
        for a in (parsed.get("alternatives") or [])
        if a.get("category") in keys and a.get("category") != cat
    ][:2]
    threshold = float(rules.get("confidence_threshold", 0.6))
    out = {"category": cat, "confidence": confidence,
           "reason": (parsed.get("reason") or "")[:200],
           "alternatives": alternatives, "raw_category": cat}
    if cat not in keys or confidence < threshold:
        out["category"] = FALLBACK
    return out


# ── Routing decision ─────────────────────────────────────────────────────────

def route(result: dict, rules: dict, auto_confidence: Optional[float] = None) -> dict:
    """Turn a classification into what to do: forward / hold / review."""
    bar = config.INFO_ROUTING_AUTO_CONFIDENCE if auto_confidence is None else auto_confidence
    cats = categories(rules)
    cat = result.get("category") or FALLBACK
    cfg = cats.get(cat) if cat != FALLBACK else None
    out = {**result, "category": cat, "forward_to": [], "cc": [],
           "display_name": display_name(rules, cat) if cfg else "Not sure",
           "group": (cfg or {}).get("group") or ""}
    if not cfg:
        guess = result.get("raw_category")
        out["route"] = "review"
        out["guess"] = guess if guess in cats else None
    elif float(result.get("confidence") or 0) < bar:
        out["route"] = "review"
        out["guess"] = cat
    elif cfg.get("action") == "forward" and recipients(cfg.get("forward_to")):
        to = recipients(cfg.get("forward_to"))
        out["route"] = "forward"
        out["forward_to"] = to
        out["cc"] = [c for c in recipients(cfg.get("cc"))
                     if c.lower() not in {t.lower() for t in to}]
    else:
        out["route"] = "hold"
    return out


async def decide(content: str, sender_email: str, subject: str,
                 lf_parent: Optional[dict] = None) -> dict:
    """Sender rule first (no AI), otherwise the AI — then route()."""
    rules = load_rules()
    key = sender_rule(sender_email, rules)
    if key:
        result = {"category": key, "confidence": 1.0, "alternatives": [],
                  "reason": f"Sent by {sender_email.strip().lower()} — sorted by sender, no AI needed.",
                  "source": "sender_rule"}
    else:
        result = {**await classify(content, sender_email, subject, lf_parent), "source": "ai"}
    return route(result, rules)


# ── Text for the note and the forward ────────────────────────────────────────

def note_header(decision: dict, rules: Optional[dict] = None) -> list[str]:
    """First lines of the private note: the category and the reason."""
    if decision.get("route") == "review":
        guess = decision.get("guess")
        head = "🗂️ **info@ routing: not sure**"
        if guess:
            name = display_name(rules or load_rules(), guess)
            pct = int(round(float(decision.get("confidence") or 0) * 100))
            head += f" — closest match: {name} ({pct}%)"
    else:
        group = decision.get("group")
        head = f"🗂️ **info@ routing: {group + ' › ' if group else ''}{decision.get('display_name')}**"
    lines = [head]
    if decision.get("reason"):
        lines.append(decision["reason"])
    return lines


def forward_body(sender_name: str, sender_email: str, subject: str, trail: str,
                 followup: bool = False, mailbox: str = "info@durian.in") -> str:
    """The email the owner receives — plain, professional, no routing jargon."""
    who = sender_name or sender_email or "the sender"
    if followup:
        intro = (f"A new email from {who} arrived in a thread we forwarded to you "
                 f"earlier. The whole thread is below; the newest email is at the end.")
    else:
        intro = (f"Forwarding the email below, received at {mailbox} from {who}, "
                 f"for your review and necessary action.")
    lines = [intro, ""]
    if sender_email:
        lines += [f"To respond, please write to {sender_email} directly — a reply "
                  f"to this email comes back to {mailbox}.", ""]
    lines += [
        f"From: {sender_name or '(unknown)'} <{sender_email}>",
        f"Subject: {subject or '(no subject)'}",
        "",
        "----------------------------------------",
        "",
        trail,
        "",
        "----------------------------------------",
        "Regards,",
        "Team Durian",
    ]
    return "\n".join(lines)


# ── Follow-ups in a forwarded thread ─────────────────────────────────────────

def _parse_iso(value) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def followup_check(state: dict, sender_email: str, message_id, now: datetime,
                   max_per_day: int, internal_domains) -> tuple[bool, str]:
    """(forward it?, why not). Only a thread we actually forwarded, only mail
    from the outside world, never machine mail, never twice, at most
    max_per_day follow-ups per thread in 24 hours."""
    if not (state or {}).get("forwarded") or not state.get("forward_to"):
        return False, "not_forwarded"
    if not (sender_email or "").strip():
        return False, "no_sender"
    if is_internal(sender_email, internal_domains):
        return False, "internal_sender"
    if is_automated(sender_email):
        return False, "automated_sender"
    if message_id is not None and str(message_id) in {str(i) for i in state.get("followup_ids") or []}:
        return False, "already_forwarded"
    recent = [t for t in (_parse_iso(x) for x in state.get("followup_times") or [])
              if t and now - t < timedelta(hours=24)]
    if len(recent) >= max_per_day:
        return False, "daily_cap"
    return True, ""


def record_followup(state: dict, message_id, now: datetime) -> dict:
    """State after forwarding a follow-up (bounded history)."""
    ids = [*(state.get("followup_ids") or []), message_id][-_FOLLOWUP_IDS_KEPT:]
    times = [*(state.get("followup_times") or []), now.isoformat()][-10:]
    return {**state, "followup_ids": ids, "followup_times": times,
            "followup_count": int(state.get("followup_count") or 0) + 1}
