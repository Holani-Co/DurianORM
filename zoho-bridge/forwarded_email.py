# Detect a staff-forwarded customer email and recover the REAL customer.
#
# Durian staff routinely forward a customer's complaint into hello@durian.in.
# Chatwoot then treats the FORWARDER as the contact, so the acknowledgement, the
# Zoho ticket and the CRM deal all get attached to the employee instead of the
# customer (see conversation #3737: Shilpi forwarded Nitin Banga's warranty
# complaint and Nitin never heard back).
#
# This module answers one question: "is this a forward, and if so who is the
# real customer?" It is deliberately conservative — when the answer isn't clear
# it says so, and the caller holds the conversation for a human rather than
# emailing a guessed address. Getting this wrong means mailing the wrong person,
# which is exactly the bug we're fixing.
#
# Stdlib only; no network. Pure functions so the parsing is unit-testable.

import re

# Markers Gmail / Outlook / Apple Mail put above the quoted original.
_FORWARD_MARKERS = (
    "---------- forwarded message",
    "-------- forwarded message",
    "begin forwarded message",
    "-----original message-----",
    "________________________________",  # Outlook's divider, checked with a From:
)

_SUBJECT_PREFIXES = ("fwd:", "fw:", "fwd :", "fw :", "forwarded:")

# "From: Nitin Banga <nitin21banga@gmail.com>" / "From: nitin@x.com" / with *bold* markup.
_FROM_LINE = re.compile(
    r"^\s*[*_>\s]*from\s*:\s*(?P<rest>.+)$",
    re.IGNORECASE | re.MULTILINE,
)
_ANGLE_EMAIL = re.compile(r"<\s*(?P<email>[^<>@\s]+@[^<>@\s]+\.[^<>@\s]+)\s*>")
_BARE_EMAIL = re.compile(r"(?P<email>[^<>@\s,;:]+@[^<>@\s,;:]+\.[^<>@\s,;:]+)")

# Indian mobile: optional +91/0 prefix, 10 digits starting 6-9. Tolerates the
# "#9999816347" the customer wrote and spaces/dashes inside the number.
_PHONE = re.compile(r"(?:\+?91[\s-]?|0)?[#\s]?([6-9]\d[\d\s-]{7,12}\d)")


def _clean_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) > 10 and digits.startswith("91"):
        digits = digits[2:]
    return digits if len(digits) == 10 else ""


def looks_forwarded(subject: str, body: str) -> bool:
    """True when the message carries a forward marker or a Fwd:/FW: subject.
    The Outlook divider alone isn't enough — it also appears in ordinary reply
    chains — so it only counts alongside an embedded From: header."""
    subj = (subject or "").strip().lower()
    if subj.startswith(_SUBJECT_PREFIXES):
        return True
    low = (body or "").lower()
    for marker in _FORWARD_MARKERS:
        if marker not in low:
            continue
        if marker.startswith("____"):
            return bool(_FROM_LINE.search(body or ""))
        return True
    return False


def _iter_from_candidates(body: str):
    """Every 'From:' line in the body, in order, as (display_name, email)."""
    for m in _FROM_LINE.finditer(body or ""):
        rest = m.group("rest").strip()
        angle = _ANGLE_EMAIL.search(rest)
        if angle:
            email = angle.group("email")
            name = rest[: angle.start()].strip(" \t*_\"'<>")
        else:
            bare = _BARE_EMAIL.search(rest)
            if not bare:
                continue
            email = bare.group("email")
            name = rest[: bare.start()].strip(" \t*_\"'<>")
        yield name.strip(), email.strip().lower()


def extract_original_sender(subject: str, body: str, internal_domains: tuple,
                            exclude_emails: tuple = ()) -> dict | None:
    """The real customer behind a forwarded email, or None when we can't tell.

    Returns {"email", "name", "phone"}. Conservative by design:
      • only looks at messages that actually look forwarded
      • skips addresses on our OWN domains — a forwarded internal thread has no
        external customer, and the forwarder must never be mistaken for one
      • skips anything in `exclude_emails` (e.g. the Chatwoot sender, our inbox)
      • returns None rather than guessing, so the caller can hand off to a human
    """
    if not looks_forwarded(subject, body):
        return None

    excluded = {e.strip().lower() for e in exclude_emails if e}
    domains = tuple(d.strip().lower().lstrip("@") for d in internal_domains if d)

    for name, email in _iter_from_candidates(body):
        if email in excluded:
            continue
        if any(email.endswith("@" + d) for d in domains):
            continue  # internal address — a colleague, not the customer
        return {"email": email, "name": name or "", "phone": find_phone(body)}
    return None


def find_phone(body: str) -> str:
    """First plausible Indian mobile in the body, normalised to 10 digits.
    Empty when nothing convincing is present."""
    for m in _PHONE.finditer(body or ""):
        cleaned = _clean_phone(m.group(1))
        if cleaned:
            return cleaned
    return ""
