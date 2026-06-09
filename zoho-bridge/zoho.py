# Zoho Desk client: OAuth token cache + ticket creation + related-ticket search.

import re
import time
from datetime import datetime, timezone  # noqa: F401 — used in type annotation strings
from typing import Optional

import httpx

import config


def _zoho_iso(dt: datetime) -> str:
    """Format a datetime in the shape Zoho Desk accepts for date-time fields
    (dueDate, etc.). Zoho expects `YYYY-MM-DDTHH:MM:SS.SSSZ` — explicit
    milliseconds + the literal 'Z' suffix. Python's default `isoformat()`
    emits `+00:00` which Zoho rejects with a 422 INVALID_DATA on /dueDate."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

_token_cache = {"value": None, "expires_at": 0.0}


# ── OAuth ─────────────────────────────────────────────────────────────────
async def get_access_token() -> str:
    """Return a cached Zoho access token, refreshing if expired (or expiring soon)."""
    if _token_cache["value"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["value"]
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            f"{config.ZOHO_ACCOUNTS_URL}/oauth/v2/token",
            params={
                "refresh_token": config.ZOHO_REFRESH_TOKEN,
                "client_id":     config.ZOHO_CLIENT_ID,
                "client_secret": config.ZOHO_CLIENT_SECRET,
                "grant_type":    "refresh_token",
            },
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Zoho token refresh failed: {data}")
        _token_cache["value"]       = data["access_token"]
        _token_cache["expires_at"]  = time.time() + data.get("expires_in", 3600)
        return _token_cache["value"]


# ── Ticket creation ───────────────────────────────────────────────────────
def _build_ticket_body(payload: dict) -> dict:
    conv     = payload.get("conversation") or payload
    contact  = (conv.get("meta") or {}).get("sender") or {}
    inbox    = (conv.get("inbox") or {}).get("name") \
               or payload.get("inbox", {}).get("name", "Chatwoot")
    messages = conv.get("messages") or []

    def label(m):
        return "Customer" if m.get("message_type") == 0 else "Agent/Bot"

    transcript = "\n".join(
        f"[{label(m)}] {m.get('content', '').strip()}"
        for m in messages if m.get("content")
    ) or "(no text messages)"

    # Prefer the trigger-message's content (top-level on the webhook payload)
    # when it's an INCOMING customer message — the conv.messages array may not
    # yet include it (timing / cache) and early entries are usually bot
    # template prompts that make a useless subject.
    trigger_content = (payload.get("content") or "").strip()
    is_trigger_incoming = payload.get("message_type") in (0, "incoming")

    if trigger_content and is_trigger_incoming:
        first_msg = trigger_content
    else:
        first_msg = next(
            (m.get("content", "") for m in messages
             if m.get("content") and m.get("message_type") in (0, "incoming")),
            ""
        )
        if not first_msg:
            first_msg = next(
                (m.get("content", "") for m in messages if m.get("content")), ""
            )
    subject = f"[{inbox}] {first_msg[:60] or 'New conversation'}"

    # Include Chatwoot team in description for context (e.g., "Legal" → Zoho agent
    # knows what kind of ticket this is even before routing inside Zoho)
    team_meta  = (conv.get("meta") or {}).get("team")
    team_label = f"\n\n— Chatwoot team: {team_meta.get('name')}" if team_meta else ""

    # Deep-link back to the originating Chatwoot conversation. Lets a Zoho agent
    # jump straight from the ticket into Chatwoot to see the full thread, reply
    # to the customer, or check newer messages that arrived after ticket
    # creation.
    #
    # IMPORTANT: builds from CHATWOOT_PUBLIC_URL, not CHATWOOT_BASE_URL.
    # BASE_URL is what the bridge uses for INTERNAL API calls (defaults to
    # localhost:3000 on single-VM deploys); PUBLIC_URL is the user-facing
    # address (e.g. https://orm.durianos.in). A Zoho agent clicking this link
    # is in a browser outside the VM, so localhost would be unreachable.
    #
    # Rendered as an HTML anchor because Zoho Desk's description field renders
    # HTML — a raw URL also works as a fallback if rendering is ever disabled.
    # Best-effort: skipped silently if conv id is missing.
    conv_id = conv.get("id")
    if conv_id:
        chatwoot_url = (
            f"{config.CHATWOOT_PUBLIC_URL.rstrip('/')}"
            f"/app/accounts/{config.CHATWOOT_ACCOUNT_ID}"
            f"/conversations/{conv_id}"
        )
        chatwoot_link = (
            f'<p><a href="{chatwoot_url}" target="_blank" rel="noopener">'
            f'➡ Open conversation in Chatwoot</a></p><hr/>'
        )
    else:
        chatwoot_link = ""

    return {
        "subject":      subject,
        "description":  chatwoot_link + transcript + team_label,
        "departmentId": config.ZOHO_DEPARTMENT_ID,
        "channel":      "Chat",
        "priority":     "Medium",
        "contact": {
            "lastName": contact.get("name") or "Unknown",
            "email":    contact.get("email")
                        or f"chatwoot-{contact.get('id', 'unknown')}@noreply.local",
            "phone":    contact.get("phone_number") or "",
        },
        "cf": {
            "cf_chatwoot_conversation_id": str(conv.get("id") or ""),
        },
    }


async def create_ticket(payload: dict, priority: str | None = None,
                        due_at: "datetime | None" = None) -> dict:
    """Create a Zoho Desk ticket from a Chatwoot webhook payload.

    Optional kwargs (used by the priority-escalation handler):
      priority: Chatwoot priority level ("urgent" / "high" / "medium" / "low").
                Mapped to Zoho enum: urgent → "Highest", others by name.
                Also prefixes the subject so it stands out in Zoho's list view.
      due_at:   datetime → Zoho's `dueDate`. Adds an SLA-style deadline.
    """
    body = _build_ticket_body(payload)

    if priority:
        pmap = {"urgent": "Highest", "high": "High",
                "medium": "Medium",  "low":  "Low"}
        body["priority"] = pmap.get(priority.lower(), body.get("priority") or "Medium")
        if not body.get("subject", "").startswith(f"[{priority.upper()}]"):
            body["subject"] = f"[{priority.upper()}] {body['subject']}"

    if due_at is not None:
        try:
            body["dueDate"] = _zoho_iso(due_at)
        except Exception:
            pass

    async def _post(client, token):
        return await client.post(
            f"{config.ZOHO_DESK_URL}/api/v1/tickets",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "orgId":         config.ZOHO_ORG_ID,
                "Content-Type":  "application/json",
            },
            json=body,
        )

    token = await get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await _post(client, token)
        if r.status_code == 401:                  # stale token → refresh once
            _token_cache["value"] = None
            r = await _post(client, await get_access_token())
        if r.status_code >= 300:
            raise RuntimeError(f"Zoho ticket create failed [{r.status_code}]: {r.text}")
        return r.json()


# ── Related-ticket search ─────────────────────────────────────────────────
# Used right after creating a ticket to surface "possibly related" past
# tickets in Chatwoot's sidebar — a duplicate-detection hint for the agent.

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at",
    "for", "with", "by", "from", "as", "is", "are", "was", "were", "be",
    "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "should", "could", "may", "might", "can", "i", "you", "your",
    "yours", "we", "our", "us", "they", "them", "their", "this", "that",
    "these", "those", "it", "its", "any", "some", "all", "no", "not",
    "very", "please", "thanks", "thank", "hi", "hello", "hey",
}


def _clean_search_query(raw: str) -> str:
    """Strip our '[Inbox] ' prefix, drop punctuation/stop-words, keep top
    keywords. Zoho's tickets/search rejects bare punctuation (an apostrophe
    in 'you're' returned 422 in early testing).

    Stays REGEX (not AI) intentionally — this is deterministic text
    normalization for a search query. Replacing it with an LLM call would
    add latency and non-determinism for zero accuracy gain."""
    s = raw or ""
    s = re.sub(r"^\s*\[[^\]]{0,40}\]\s*", "", s)
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", s).lower()
    words = [w for w in s.split() if len(w) >= 3 and w not in _STOPWORDS]
    return " ".join(words[:6])


async def search_tickets(query: str, exclude_id: Optional[str] = None,
                         limit: int = 3) -> list[dict]:
    """Search Zoho Desk tickets by free-text query against the SUBJECT field.
    Returns top matches (Zoho ranks by relevance), excluding exclude_id so a
    freshly-created ticket doesn't match itself.

    Best-effort: returns [] on any failure (network, no-results, 4xx)."""
    q = _clean_search_query(query)
    if not q:
        return []

    async def _get(client, token):
        # Zoho Desk's /tickets/search takes field-name params directly
        # (?subject=keyword), NOT a searchStr wrapper.
        return await client.get(
            f"{config.ZOHO_DESK_URL}/api/v1/tickets/search",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "orgId":         config.ZOHO_ORG_ID,
            },
            params={
                "subject":      q,
                "limit":        str(limit + 1),
                "departmentId": config.ZOHO_DEPARTMENT_ID,
            },
        )

    try:
        token = await get_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            r = await _get(client, token)
            if r.status_code == 401:
                _token_cache["value"] = None
                r = await _get(client, await get_access_token())
            if r.status_code == 204:
                return []
            if r.status_code >= 300:
                print(f"[zoho] search_tickets non-200 [{r.status_code}]; "
                      f"query={q!r} body={r.text[:500]!r}")
                return []
            results = (r.json() or {}).get("data") or []
    except Exception as e:
        print(f"[zoho] search_tickets exception: {type(e).__name__}: {e}")
        return []

    if exclude_id is not None:
        results = [t for t in results if str(t.get("id")) != str(exclude_id)]

    out = []
    for t in results[:limit]:
        tid = t.get("id")
        out.append({
            "id":         tid,
            "number":     t.get("ticketNumber"),
            "subject":    t.get("subject") or "",
            "status":     t.get("status"),
            "url":        t.get("webUrl") or (
                f"{config.ZOHO_DESK_URL}/agent/tickets/details/{tid}" if tid else None
            ),
            "created_at": t.get("createdTime"),
        })
    return out
