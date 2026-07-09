# Zoho Desk client: OAuth token cache + ticket creation + related-ticket search.

import html
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
def _build_ticket_body(payload: dict, messages: list | None = None,
                       summary: dict | None = None) -> dict:
    conv     = payload.get("conversation") or payload
    contact  = (conv.get("meta") or {}).get("sender") or {}
    # Prefer the FULL transcript fetched from the API (passed in by the
    # caller); fall back to the sparse messages on the webhook payload.
    # The payload's array is often just the single triggering message — on a
    # manual handoff that's the bot's "teammate will follow up" line, which
    # is why pre-summary tickets had useless subjects + transcripts.
    messages = messages if messages is not None else (conv.get("messages") or [])
    summary  = summary or {}

    def esc(text):
        return html.escape(str(text or ""))

    def label(m):
        return "Customer" if m.get("message_type") in (0, "incoming") \
            else config.AI_AGENT_NAME

    def msg_html(m):
        content = esc(m.get("content", "").strip())
        content_html = content.replace("\n", "<br>")
        return f"<p><b>[{label(m)}]</b><br>{content_html}</p>"

    transcript = "".join(
        msg_html(m) for m in messages if m.get("content")
    ) or "<p>(no text messages)</p>"

    # Subject priority:
    #   1. AI summary's one-line issue (best — the customer's actual problem)
    #   2. trigger message IF it's an incoming customer message
    #   3. first incoming message in the transcript
    #   4. any message / fallback
    # The bot's outgoing handoff line is never used as the subject.
    trigger_content = (payload.get("content") or "").strip()
    is_trigger_incoming = payload.get("message_type") in (0, "incoming")

    summary_subject = (summary.get("subject") or "").strip()
    if summary_subject:
        first_msg = summary_subject
    elif trigger_content and is_trigger_incoming:
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
    # Brand the subject with the product name (not the inbox name) so every
    # ticket reads "[DurianORM] …" in Zoho's list view.
    subject = f"[{config.PRODUCT_NAME}] {first_msg[:80] or 'New conversation'}"

    # AI summary block — headlines the ticket so the agent sees the issue,
    # goal, and recommended next step without reading the thread. Rendered
    # as HTML (Zoho Desk renders the description as HTML). Empty when the
    # summariser had nothing usable.
    summary_block = ""
    if summary.get("summary") or summary.get("customer_goal") or summary.get("next_step"):
        parts = ["<p><b>📋 Summary (AI-generated)</b></p><ul>"]
        if summary.get("summary"):
            parts.append(f"<li><b>What happened:</b> {esc(summary['summary'])}</li>")
        if summary.get("customer_goal"):
            parts.append(f"<li><b>Customer wants:</b> {esc(summary['customer_goal'])}</li>")
        if summary.get("next_step"):
            parts.append(f"<li><b>Suggested next step:</b> {esc(summary['next_step'])}</li>")
        parts.append("</ul><hr/>")
        summary_block = "".join(parts)

    # Include Chatwoot team in description for context (e.g., "Legal" → Zoho agent
    # knows what kind of ticket this is even before routing inside Zoho)
    team_meta  = (conv.get("meta") or {}).get("team")
    team_label = f"<p><i>— Chatwoot team: {esc(team_meta.get('name'))}</i></p>" if team_meta else ""

    # Ride-along: extracted bill/receipt data (document_extractor pipeline,
    # stored on custom_attributes.extracted_documents). Surfacing it in the
    # ticket description means the Zoho agent sees order id / amount / issue
    # without opening Chatwoot. Best-effort; absent for most conversations.
    docs = (conv.get("custom_attributes") or {}).get("extracted_documents") or []
    doc_lines = []
    for d in docs[:5]:
        if not isinstance(d, dict):
            continue
        bits = [(d.get("document_type") or "document").replace("_", " ")]
        if d.get("order_id"):
            bits.append(f"order {d['order_id']}")
        if d.get("invoice_number"):
            bits.append(f"invoice {d['invoice_number']}")
        if d.get("amount"):
            bits.append(f"{d.get('currency') or ''} {d['amount']}".strip())
        if d.get("document_date"):
            bits.append(d["document_date"])
        if d.get("issue_hint"):
            bits.append(f"issue: {d['issue_hint']}")
        doc_lines.append("  • " + " · ".join(bits))
    docs_label = ("<p><i>— Extracted documents:</i><br>" + "<br>".join(doc_lines) + "</p>") if doc_lines else ""

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
        "description":  summary_block + chatwoot_link + transcript + team_label + docs_label,
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
                        due_at: "datetime | None" = None,
                        messages: list | None = None,
                        summary: dict | None = None,
                        assignee_id: str | None = None) -> dict:
    """Create a Zoho Desk ticket from a Chatwoot webhook payload.

    Optional kwargs:
      priority: Chatwoot priority level ("urgent" / "high" / "medium" / "low").
                Mapped to Zoho enum (High/Medium/Low): urgent + high → "High".
                Also prefixes the subject so it stands out in Zoho's list view.
      due_at:   datetime → Zoho's `dueDate`. Adds an SLA-style deadline.
      messages: full conversation transcript fetched from the Chatwoot API
                (overrides the sparse messages on the webhook payload).
      summary:  AI summary dict {subject, summary, customer_goal, next_step}
                used to headline the ticket and title it by the customer's
                actual issue rather than the bot's handoff line.
      assignee_id: Zoho Desk AGENT id to own the ticket (`assigneeId`) — e.g.
                the customersupport agent who handles product complaints. This
                is a Desk agent id (separate from any CRM id); the agent must
                belong to the ticket's department or Zoho ignores it. Omitted →
                unassigned in the department (prior behaviour).
    """
    body = _build_ticket_body(payload, messages=messages, summary=summary)

    if assignee_id:
        body["assigneeId"] = str(assignee_id)

    if priority:
        # Zoho Desk only has High / Medium / Low — it has no "Highest", so
        # sending that silently falls back to Medium. Map urgent → High.
        pmap = {"urgent": "High", "high": "High",
                "medium": "Medium", "low": "Low"}
        body["priority"] = pmap.get(priority.lower(), body.get("priority") or "Medium")
        if not body.get("subject", "").startswith(f"[{priority.upper()}]"):
            body["subject"] = f"[{priority.upper()}] {body['subject']}"

    # Always headline the ticket with WHY it exists (priority) and WHO owned the
    # conversation in Chatwoot — so the priority shows on EVERY ticket (not just
    # escalations) and the Zoho agent sees the handling agent. Unassigned
    # conversations were handled by the bridge itself → credit the AI agent.
    conv_meta  = (payload.get("conversation") or payload).get("meta") or {}
    handled_by = (conv_meta.get("assignee") or {}).get("name") or config.AI_AGENT_NAME
    meta_html = (
        f"<p><b>Priority:</b> {html.escape(body.get('priority') or 'Medium')}"
        f"<br><b>Handled by:</b> {html.escape(handled_by)}</p><hr/>"
    )
    body["description"] = meta_html + (body.get("description") or "")

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


def _ticket_brief(t: dict) -> dict:
    """Normalize a raw Zoho ticket record to the small dict the bridge passes
    around (candidates list, pending_zoho_ticket attr, private notes)."""
    tid = t.get("id")
    return {
        "id":         tid,
        "number":     t.get("ticketNumber"),
        "subject":    t.get("subject") or "",
        "status":     t.get("status"),
        "url":        t.get("webUrl") or (
            f"{config.ZOHO_DESK_URL}/agent/tickets/details/{tid}" if tid else None
        ),
        "created_at": t.get("createdTime"),
    }


async def _search_tickets_raw(params: dict) -> list[dict]:
    """GET /tickets/search with the standard headers + one 401 retry.
    Returns raw ticket records; [] on any failure (best-effort, never raises)."""
    async def _get(client, token):
        return await client.get(
            f"{config.ZOHO_DESK_URL}/api/v1/tickets/search",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "orgId":         config.ZOHO_ORG_ID,
            },
            params={**params, "departmentId": config.ZOHO_DEPARTMENT_ID},
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
                print(f"[zoho] tickets/search non-200 [{r.status_code}]; "
                      f"params={params!r} body={r.text[:300]!r}")
                return []
            return (r.json() or {}).get("data") or []
    except Exception as e:
        print(f"[zoho] tickets/search exception: {type(e).__name__}: {e}")
        return []


async def get_ticket_by_number(number: str) -> Optional[dict]:
    """Exact lookup by the human-facing ticketNumber (the '#253' customers
    quote from their emails — NOT the long internal id). None if no match."""
    number = str(number).strip().lstrip("#")
    if not number.isdigit():
        return None
    results = await _search_tickets_raw({"ticketNumber": number, "limit": "1"})
    return _ticket_brief(results[0]) if results else None


async def search_tickets_by_content(subject: str, body: str,
                                    limit: int = 3,
                                    open_only: bool = True) -> list[dict]:
    """Find tickets whose DESCRIPTION matches the incoming message's keywords.

    The description holds the original email text (create_ticket embeds the
    message thread), so a customer re-sending the same complaint from a
    DIFFERENT email address still matches — unlike the subject field, where
    the AI-generated ticket subject paraphrases and Zoho's AND-semantics
    keyword match misses it (probed 2026-07-09: description search found the
    re-sent complaint, subject search returned 204).

    Body keywords are the primary query; the raw subject is a secondary
    query for same-subject re-sends. open_only drops closed tickets — a
    closed ticket isn't a duplicate risk."""
    seen: set[str] = set()
    out: list[dict] = []
    body_q    = _clean_search_query(body)
    subject_q = _clean_search_query(subject)
    queries = [p for p in (
        {"description": body_q} if body_q else None,
        {"subject": subject_q}  if subject_q else None,
    ) if p]
    for params in queries:
        for t in await _search_tickets_raw({**params, "limit": str(limit + 2)}):
            status = str(t.get("status") or "").lower()
            if open_only and status not in _OPEN_STATUSES:
                continue
            tid = str(t.get("id"))
            if tid in seen:
                continue
            seen.add(tid)
            out.append(_ticket_brief(t))
            if len(out) >= limit:
                return out
    return out


async def search_tickets(query: str, exclude_id: Optional[str] = None,
                         limit: int = 3) -> list[dict]:
    """Search Zoho Desk tickets by free-text query against the SUBJECT field.
    Returns top matches (Zoho ranks by relevance), excluding exclude_id so a
    freshly-created ticket doesn't match itself.

    Best-effort: returns [] on any failure (network, no-results, 4xx)."""
    q = _clean_search_query(query)
    if not q:
        return []
    # Zoho Desk's /tickets/search takes field-name params directly
    # (?subject=keyword), NOT a searchStr wrapper.
    results = await _search_tickets_raw({"subject": q, "limit": str(limit + 1)})
    if exclude_id is not None:
        results = [t for t in results if str(t.get("id")) != str(exclude_id)]
    return [_ticket_brief(t) for t in results[:limit]]


# ── Open-ticket lookup by contact email ───────────────────────────────────
# Used by the ticket-dedup feature: when we're about to auto-create a ticket
# for a contact who already has one or more open tickets, the bridge pauses
# and asks the agent whether to create-new or attach-to-existing.
#
# Zoho stores the email on the CONTACT record, not denormalised onto each
# ticket — tickets created by create_ticket() carry email=None and link to
# the contact via contactId. So `/tickets/search?email=` returns nothing.
# The correct path is two hops:
#   1. GET /contacts/search?email=  → resolve the Zoho contact id
#   2. GET /contacts/{id}/tickets   → list that contact's tickets
# Status filter ("Open" / "On Hold") is applied client-side; a closed ticket
# isn't a duplicate-risk, it's done.
async def _resolve_contact_id(client, token, email: str) -> Optional[str]:
    """Resolve a Zoho Desk contact id from an email. None if not found."""
    r = await client.get(
        f"{config.ZOHO_DESK_URL}/api/v1/contacts/search",
        headers={"Authorization": f"Zoho-oauthtoken {token}",
                 "orgId": config.ZOHO_ORG_ID},
        params={"email": email, "limit": "1"},
    )
    if r.status_code == 401:
        _token_cache["value"] = None
        token = await get_access_token()
        r = await client.get(
            f"{config.ZOHO_DESK_URL}/api/v1/contacts/search",
            headers={"Authorization": f"Zoho-oauthtoken {token}",
                     "orgId": config.ZOHO_ORG_ID},
            params={"email": email, "limit": "1"},
        )
    if r.status_code == 204 or r.status_code >= 300:
        return None
    data = (r.json() or {}).get("data") or []
    return str(data[0]["id"]) if data and data[0].get("id") else None


_OPEN_STATUSES = {"open", "on hold"}


async def search_open_tickets_by_email(email: str,
                                       limit: int = 5,
                                       exclude_id: Optional[str] = None) -> list[dict]:
    """Return open / on-hold Zoho Desk tickets for the contact with this email.

    Best-effort: returns [] on any failure (no email, no matching contact,
    network error, 4xx)."""
    if not email:
        return []

    results: list[dict] = []
    try:
        token = await get_access_token()
        async with httpx.AsyncClient(timeout=15) as client:
            contact_id = await _resolve_contact_id(client, token, email)
            if not contact_id:
                return []

            r = await client.get(
                f"{config.ZOHO_DESK_URL}/api/v1/contacts/{contact_id}/tickets",
                headers={"Authorization": f"Zoho-oauthtoken {token}",
                         "orgId": config.ZOHO_ORG_ID},
                # Pull a generous page; we filter to open statuses client-side
                # and sort newest-first below.
                params={"limit": "50"},
            )
            if r.status_code == 401:
                _token_cache["value"] = None
                token = await get_access_token()
                r = await client.get(
                    f"{config.ZOHO_DESK_URL}/api/v1/contacts/{contact_id}/tickets",
                    headers={"Authorization": f"Zoho-oauthtoken {token}",
                             "orgId": config.ZOHO_ORG_ID},
                    params={"limit": "50"},
                )
            if r.status_code == 204:
                return []
            if r.status_code >= 300:
                print(f"[zoho] contact tickets lookup non-200 [{r.status_code}] "
                      f"for {email!r}: {r.text[:300]!r}")
                return []
            all_tickets = (r.json() or {}).get("data") or []
            results = [t for t in all_tickets
                       if str(t.get("status") or "").lower() in _OPEN_STATUSES]
            # Newest first by createdTime (string ISO sorts correctly).
            results.sort(key=lambda t: t.get("createdTime") or "", reverse=True)
    except Exception as e:
        print(f"[zoho] search_open_tickets_by_email exception: {type(e).__name__}: {e}")
        return []

    if exclude_id is not None:
        results = [t for t in results if str(t.get("id")) != str(exclude_id)]
    return [_ticket_brief(t) for t in results[:limit]]


# ── Append a comment on an existing ticket ────────────────────────────────
# Used when the agent picks "Attach to existing" — we don't create a new
# ticket; instead we add a comment on the existing one summarising the new
# conversation, so the existing ticket history stays the source of truth.
async def add_comment_to_ticket(ticket_id: str, body_html: str,
                                is_public: bool = False) -> dict:
    """POST /api/v1/tickets/{id}/comments. Raises on failure."""
    async def _post(client, token):
        return await client.post(
            f"{config.ZOHO_DESK_URL}/api/v1/tickets/{ticket_id}/comments",
            headers={
                "Authorization": f"Zoho-oauthtoken {token}",
                "orgId":         config.ZOHO_ORG_ID,
                "Content-Type":  "application/json",
            },
            json={
                "content":     body_html,
                "contentType": "html",
                "isPublic":    is_public,
            },
        )

    token = await get_access_token()
    async with httpx.AsyncClient(timeout=15) as client:
        r = await _post(client, token)
        if r.status_code == 401:
            _token_cache["value"] = None
            r = await _post(client, await get_access_token())
        if r.status_code >= 300:
            raise RuntimeError(
                f"Zoho add_comment failed [{r.status_code}]: {r.text[:300]}"
            )
        return r.json()
