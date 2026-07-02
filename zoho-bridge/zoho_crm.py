# Zoho CRM client: OAuth token cache + Contact find/create + Note attach.
#
# Mirrors zoho.py's shape but for the CRM product. Uses a SEPARATE refresh
# token (ZOHO_CRM_REFRESH_TOKEN) so Desk's token stays untouched — a CRM auth
# issue can't take down ticket creation. The OAuth client (client_id +
# client_secret) is shared with Desk because it's the same Self Client.
#
# Zoho CRM v6 API: https://www.zohoapis.<region>/crm/v6/
#   POST   /{Module}                     — insert 1+ records
#   GET    /{Module}/search?criteria=…   — search by field
#   POST   /{Module}/{id}/Notes          — attach a note to a specific record
# All responses wrap results in {"data": [{"code": "SUCCESS", "details": {...}}]}.
#
# Contact + Note is the FOUNDATION shared by every CRM push flow: auto
# (product enquiry → Contact+Note automatically) AND manual (button-driven
# "Push to CRM as Lead/Deal" links back to this contact). Keeping it as one
# well-tested module means Lead/Deal (PR B) reuse the same code paths.

import html
import re
import time
from typing import Optional

import httpx

import config


_token_cache = {"value": None, "expires_at": 0.0}


# ── OAuth ─────────────────────────────────────────────────────────────────
async def get_access_token() -> str:
    """Return a cached CRM access token, refreshing shortly before expiry.

    Uses a separate refresh token (ZOHO_CRM_REFRESH_TOKEN) from Desk so a
    CRM-side token issue never breaks ticket creation."""
    if _token_cache["value"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["value"]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{config.ZOHO_CRM_ACCOUNTS_URL}/oauth/v2/token",
            params={
                "refresh_token": config.ZOHO_CRM_REFRESH_TOKEN,
                "client_id":     config.ZOHO_CRM_CLIENT_ID,
                "client_secret": config.ZOHO_CRM_CLIENT_SECRET,
                "grant_type":    "refresh_token",
            },
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Zoho CRM token refresh failed: {data}")
        _token_cache["value"]      = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        return _token_cache["value"]


async def _crm_request(method: str, path: str, *, params: Optional[dict] = None,
                       json_body: Optional[dict] = None) -> dict:
    """Wrap a Zoho CRM v6 request with auth + JSON parsing.

    Raises RuntimeError with the CRM error body on non-2xx so callers see the
    exact 'INVALID_DATA' / 'DUPLICATE_DATA' etc. reason in logs."""
    token = await get_access_token()
    url = f"{config.ZOHO_CRM_API_DOMAIN.rstrip('/')}/crm/v6{path}"
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.request(method, url, headers=headers,
                                 params=params, json=json_body)
        # 204 = no data (e.g. empty search). Return an empty envelope so
        # callers don't have to special-case status codes.
        if r.status_code == 204:
            return {"data": []}
        if r.status_code >= 300:
            raise RuntimeError(
                f"Zoho CRM {method} {path} failed [{r.status_code}]: {r.text}")
        return r.json()


# ── Contact ───────────────────────────────────────────────────────────────
def _split_name(full_name: str, email: str) -> tuple[str, str]:
    """Split 'John Doe' → ('John', 'Doe'). Zoho requires Last_Name; falls back
    to the email local-part when no name is given so we NEVER create a
    contact with 'Unknown' as the last name."""
    name = (full_name or "").strip()
    if name:
        parts = name.split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[1:])
        return "", parts[0]
    local = (email or "").split("@", 1)[0]
    return "", local or "Customer"


async def search_contact_by_email(email: str) -> Optional[dict]:
    """Return the first CRM Contact matching this email, or None."""
    if not email:
        return None
    # Zoho search criteria syntax: (Field:equals:value). Email must be
    # URL-encoded — httpx handles that via params, but the criteria string
    # itself is a raw expression, not query params.
    params = {"email": email}
    try:
        # /Contacts/search?email=… is a simpler equality search than criteria=;
        # supports email/phone/word natively without the DSL.
        resp = await _crm_request("GET", "/Contacts/search", params=params)
    except RuntimeError as e:
        # 400 REQUIRED_PARAM_MISSING / other CRM errors mean no result; log
        # and treat as "not found" so we fall through to create.
        print(f"[crm] contact search error for {email!r}: {e}")
        return None
    data = resp.get("data") or []
    return data[0] if data else None


async def create_contact(sender_email: str, sender_name: str,
                         phone: str = "", source: str = "Chatwoot",
                         owner_id: str = "") -> dict:
    """Create a new CRM Contact. Returns the created record ({id, …}).

    owner_id (a Zoho user id) assigns the record to a location's salesperson —
    the record shows up under their name/territory instead of the API user."""
    first, last = _split_name(sender_name, sender_email)
    record = {
        "Last_Name":  last,
        "First_Name": first,
        "Email":      sender_email,
        "Lead_Source": source,
    }
    if phone:
        record["Phone"] = phone
    if owner_id:
        record["Owner"] = {"id": str(owner_id)}
    resp = await _crm_request("POST", "/Contacts", json_body={"data": [record]})
    entry = (resp.get("data") or [{}])[0]
    if entry.get("code") != "SUCCESS":
        raise RuntimeError(f"Zoho CRM create_contact failed: {entry}")
    return entry.get("details") or {}


def _duplicate_id_from_error(err_text: str) -> Optional[str]:
    """Zoho's search index lags real-time by ~30-60s, so a just-created contact
    isn't findable via /search for a moment. When we try to re-create it
    Zoho returns a helpful DUPLICATE_DATA error with the existing record's
    id in details.duplicate_record.id — we parse and reuse it, so idempotency
    survives the search-index race."""
    import json
    try:
        # err_text is the RuntimeError message; the JSON body follows the
        # first '{' in the string.
        start = err_text.index("{")
        payload = json.loads(err_text[start:])
    except (ValueError, json.JSONDecodeError):
        return None
    entries = payload.get("data") or []
    if not entries or entries[0].get("code") != "DUPLICATE_DATA":
        return None
    dup = (entries[0].get("details") or {}).get("duplicate_record") or {}
    dup_id = dup.get("id")
    return str(dup_id) if dup_id else None


async def find_or_create_contact(sender_email: str, sender_name: str,
                                 phone: str = "",
                                 owner_id: str = "") -> tuple[str, bool]:
    """Return (contact_id, created). Idempotent — a repeat call for the same
    email returns the existing id and created=False, even when Zoho's search
    index hasn't caught up yet (we recover from the DUPLICATE_DATA response).

    owner_id (if given) assigns a newly-created Contact to that Zoho user. Note:
    a REUSED contact keeps its current owner — we don't reassign existing
    records (that would fight manual reassignments sales made).

    Empty email → returns ("", False). CRM requires an email to dedup, and
    creating an emailless contact would be worse than skipping the push."""
    if not sender_email:
        return "", False
    found = await search_contact_by_email(sender_email)
    if found:
        return str(found.get("id") or ""), False
    try:
        created = await create_contact(sender_email, sender_name, phone,
                                       owner_id=owner_id)
        return str(created.get("id") or ""), True
    except RuntimeError as e:
        # Search index lag → creation collided. Reuse the existing record.
        dup_id = _duplicate_id_from_error(str(e))
        if dup_id:
            print(f"[crm] DUPLICATE_DATA reconciled for {sender_email} → {dup_id}")
            return dup_id, False
        raise


# ── Note ──────────────────────────────────────────────────────────────────
_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(content: str) -> str:
    """Strip HTML tags + unescape entities. Zoho notes can hold HTML but the
    salesperson reads them in a compact side-panel — plain text is cleaner,
    and we're not styling anything meaningful in the note body."""
    txt = _TAG_RE.sub("", content or "")
    txt = html.unescape(txt)
    # Collapse runs of blank lines that HTML→text produces.
    txt = re.sub(r"\n{3,}", "\n\n", txt).strip()
    return txt


# Zoho Notes have a documented 32k char limit on Note_Content; we cap well
# below so a long email body doesn't push us near the limit.
NOTE_CONTENT_MAX = 8000


async def create_note(parent_module: str, parent_id: str,
                      title: str, content: str) -> dict:
    """Attach a Note to a CRM record (Contact/Lead/Deal).

    parent_module = 'Contacts' | 'Leads' | 'Deals'. Content is truncated at
    NOTE_CONTENT_MAX so a huge email body can't push us near Zoho's 32k
    limit."""
    body = (content or "")[:NOTE_CONTENT_MAX]
    if len(content or "") > NOTE_CONTENT_MAX:
        body += "\n\n… (truncated)"
    record = {"Note_Title": (title or "Note")[:200], "Note_Content": body}
    resp = await _crm_request(
        "POST", f"/{parent_module}/{parent_id}/Notes",
        json_body={"data": [record]})
    entry = (resp.get("data") or [{}])[0]
    if entry.get("code") != "SUCCESS":
        raise RuntimeError(f"Zoho CRM create_note failed: {entry}")
    return entry.get("details") or {}


# NOTE: no create_lead — the client treats Leads and Deals as the same thing,
# so the integration only creates Contacts (+Notes) and Deals.


# ── Deal layouts ──────────────────────────────────────────────────────────
# The client's Deals module has two record layouts: "Standard" (regular sales)
# and "Home Studio" (full home customization → designers). Layout ids are
# org-specific, so we resolve them by NAME once and cache for the process
# lifetime. Requires the ZohoCRM.settings.layouts.READ scope — without it the
# lookup fails and deals are created on the module's default layout (Standard),
# with a log line so the gap is visible.
_layout_cache: dict = {}


async def get_deal_layout_id(layout_name: str) -> str:
    """Return the Deals-module layout id for `layout_name`, or "" if the
    layout doesn't exist / the token lacks the layouts.READ scope."""
    if not layout_name:
        return ""
    if not _layout_cache:
        try:
            resp = await _crm_request("GET", "/settings/layouts",
                                      params={"module": "Deals"})
            for lay in resp.get("layouts") or []:
                _layout_cache[(lay.get("name") or "").strip().lower()] = \
                    str(lay.get("id") or "")
        except Exception as e:
            print(f"[crm] Deals layout lookup failed (creating on default "
                  f"layout): {e}")
            _layout_cache["__failed__"] = ""
    return _layout_cache.get(layout_name.strip().lower(), "")


# ── Deal ──────────────────────────────────────────────────────────────────
async def create_deal(contact_id: str, deal_name: str,
                      description: str, stage: str = "",
                      source: str = "Chatwoot", owner_id: str = "",
                      vertical: str = "", layout_name: str = "",
                      extra_fields: dict | None = None) -> dict:
    """Create a CRM Deal linked to a Contact. Zoho requires Deal_Name + Stage.
    Stage falls back to config.ZOHO_CRM_DEAL_DEFAULT_STAGE — set that to your
    pipeline's first stage (e.g. 'Qualification'). If the stage doesn't exist
    in your CRM you get a clear INVALID_DATA error identifying the field.

    owner_id assigns the Deal to a location's salesperson. vertical
    (Furniture / Doors, from the client matrix) is written to the field named
    by ZOHO_CRM_VERTICAL_FIELD when that's configured — otherwise it only
    appears in the Description. layout_name ("Standard" / "Home Studio")
    selects the Deal record layout; unresolvable → Zoho's default layout.
    extra_fields merges arbitrary field→value pairs into the record — the
    escape hatch for org-specific MANDATORY custom fields (e.g. the client's
    Business_Type_New picklist) without another signature change."""
    record = {
        "Deal_Name":   (deal_name or "Chatwoot Deal")[:255],
        "Stage":       stage or config.ZOHO_CRM_DEAL_DEFAULT_STAGE,
        "Lead_Source": source,
        "Description": (description or "")[:NOTE_CONTENT_MAX],
    }
    if contact_id:
        # Link the Deal to the Contact — Zoho's "Contact_Name" field on Deals
        # is a lookup (accepts {"id": "..."} shape).
        record["Contact_Name"] = {"id": contact_id}
    if owner_id:
        record["Owner"] = {"id": str(owner_id)}
    if vertical and config.ZOHO_CRM_VERTICAL_FIELD:
        record[config.ZOHO_CRM_VERTICAL_FIELD] = vertical
    if extra_fields:
        record.update(extra_fields)
    if layout_name:
        layout_id = await get_deal_layout_id(layout_name)
        if layout_id:
            record["Layout"] = {"id": layout_id}
    resp = await _crm_request("POST", "/Deals", json_body={"data": [record]})
    entry = (resp.get("data") or [{}])[0]
    if entry.get("code") != "SUCCESS":
        raise RuntimeError(f"Zoho CRM create_deal failed: {entry}")
    return entry.get("details") or {}


# ── URL helpers ───────────────────────────────────────────────────────────
def _ui_base() -> str:
    """CRM UI domain derived from the API domain (prod or sandbox aware)."""
    api = config.ZOHO_CRM_API_DOMAIN.rstrip("/")
    # www.zohoapis.in → crm.zoho.in ; sandbox.zohoapis.in → crmsandbox.zoho.in
    return (api.replace("://www.zohoapis.", "://crm.zoho.")
              .replace("://sandbox.zohoapis.", "://crmsandbox.zoho."))


def contact_url(contact_id: str) -> str:
    """Deep-link into a Contact record in Zoho CRM."""
    return f"{_ui_base()}/crm/tab/Contacts/{contact_id}" if contact_id else ""


def deal_url(deal_id: str) -> str:
    return f"{_ui_base()}/crm/tab/Potentials/{deal_id}" if deal_id else ""
