# Chatwoot Application API client. Add more methods here as needed.
# Docs: https://www.chatwoot.com/developers/api/

from typing import Optional

import httpx

import config


def _headers() -> dict:
    return {
        "api_access_token": config.CHATWOOT_API_TOKEN,
        "Content-Type":     "application/json",
    }


def _conv_url(conversation_id: int, suffix: str = "") -> str:
    return (
        f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}"
        f"/conversations/{conversation_id}{suffix}"
    )


async def assign_team(conversation_id: int, team_id: int) -> dict:
    """Assign a team to a conversation. Idempotent — Chatwoot overwrites."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            _conv_url(conversation_id, "/assignments"),
            headers=_headers(),
            json={"team_id": team_id},
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot assign_team failed [{r.status_code}]: {r.text}")
        return r.json()


async def add_label(conversation_id: int, label: str) -> dict:
    """Append a label to a conversation without removing existing ones."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _conv_url(conversation_id, "/labels"),
            headers=_headers(),
        )
        existing = r.json().get("payload", []) if r.status_code < 300 else []
        if label in existing:
            return {"labels": existing}
        merged = existing + [label]
        r = await client.post(
            _conv_url(conversation_id, "/labels"),
            headers=_headers(),
            json={"labels": merged},
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot add_label failed [{r.status_code}]: {r.text}")
        return r.json()


# ── API-channel helpers (used by the Google Reviews ingest) ────────────────
# An API-channel inbox lets us push arbitrary external messages (reviews) in as
# conversations. Flow: create/find contact → create conversation → add message.

def _acct_url(suffix: str) -> str:
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


async def list_canned_responses() -> list[dict]:
    """Return all canned responses as [{short_code, content}, ...].

    The Durian reply templates live here (seeded by setup_review_templates.py).
    The team edits them from the Chatwoot UI; the AI suggester reads them live
    at reply time, so a UI edit changes the AI's drafts with no code change.
    Returns [] on any failure (best-effort — the suggester falls back to a
    generic draft)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(_acct_url("/canned_responses"), headers=_headers())
        if r.status_code >= 300:
            print(f"[chatwoot] list_canned_responses non-200 "
                  f"[{r.status_code}]: {r.text[:200]}")
            return []
        body = r.json()
        return body if isinstance(body, list) else body.get("payload", [])


async def create_contact(name: str, identifier: str, inbox_id: int,
                         custom_attributes: dict | None = None) -> tuple[int, str]:
    """Create (or return existing) a contact on an inbox. Returns (contact_id, source_id).
    `identifier` should be stable per reviewer so re-ingests don't duplicate."""
    payload = {
        "inbox_id": inbox_id,
        "name": name or "Google user",
        "identifier": identifier,
        "custom_attributes": custom_attributes or {},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(_acct_url("/contacts"), headers=_headers(), json=payload)
        # Chatwoot returns 422 if identifier already exists → look it up instead.
        if r.status_code == 422:
            s = await client.get(
                _acct_url("/contacts/search"), headers=_headers(),
                params={"q": identifier},
            )
            s.raise_for_status()
            hits = s.json().get("payload", [])
            if not hits:
                raise RuntimeError(f"Chatwoot contact 422 and no search hit for {identifier}")
            contact = hits[0]
            ci = (contact.get("contact_inboxes") or [{}])[0]
            return contact["id"], ci.get("source_id", "")
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot create_contact failed [{r.status_code}]: {r.text}")
        contact = r.json().get("payload", {}).get("contact", {})
        ci = (contact.get("contact_inboxes") or [{}])[0]
        return contact["id"], ci.get("source_id", "")


async def create_conversation(source_id: str, inbox_id: int, contact_id: int,
                              additional_attributes: dict | None = None,
                              custom_attributes: dict | None = None) -> int:
    payload = {
        "source_id": source_id,
        "inbox_id": inbox_id,
        "contact_id": contact_id,
        "additional_attributes": additional_attributes or {},
        "custom_attributes": custom_attributes or {},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(_acct_url("/conversations"), headers=_headers(), json=payload)
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot create_conversation failed [{r.status_code}]: {r.text}")
        return r.json()["id"]


async def create_message(conversation_id: int, content: str,
                         message_type: str = "incoming", private: bool = False,
                         content_attributes: dict | None = None) -> dict:
    payload = {
        "content": content,
        "message_type": message_type,
        "private": private,
    }
    if content_attributes:
        payload["content_attributes"] = content_attributes
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            _conv_url(conversation_id, "/messages"), headers=_headers(), json=payload
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot create_message failed [{r.status_code}]: {r.text}")
        return r.json()


async def toggle_status(conversation_id: int, status: str,
                        snoozed_until: str | None = None) -> dict:
    """status ∈ {open, resolved, pending, snoozed}.

    When status='snoozed', Chatwoot moves the conversation to the Snoozed
    tab (separate from Resolved — so spam doesn't pollute Resolved-tab
    reports). `snoozed_until` controls auto-reopen:
      None  → snooze until the customer next replies (best for spam)
      ISO ts → snooze until that timestamp
    """
    payload: dict = {"status": status}
    if status == "snoozed":
        payload["snoozed_until"] = snoozed_until
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            _conv_url(conversation_id, "/toggle_status"),
            headers=_headers(), json=payload,
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot toggle_status failed [{r.status_code}]: {r.text}")
        return r.json()


async def get_contact_conversations(contact_id: int) -> list[dict]:
    """Return the list of conversations belonging to a contact. Used by the
    spam classifier as a tiebreaker for low-confidence verdicts — a contact
    with prior non-spam conversations is more likely to be a real customer.

    Returns empty list on any failure (best-effort: better to over-classify
    than to crash the webhook)."""
    if not contact_id:
        return []
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(
            _acct_url(f"/contacts/{contact_id}/conversations"),
            headers=_headers(),
        )
        if r.status_code >= 300:
            print(
                f"[chatwoot] get_contact_conversations({contact_id}) non-200 "
                f"[{r.status_code}]: {r.text[:200]}"
            )
            return []
        body = r.json()
        if isinstance(body, list):
            return body
        return body.get("payload") or body.get("data") or []


async def search_snoozed_spam_since(since_iso: str = "") -> list[dict]:
    """Return SNOOZED conversations carrying a 'spam' label, used by the
    /spam-digest endpoint to build the daily review summary.

    `since_iso` is applied as a CLIENT-SIDE filter on each conversation's
    `last_activity_at`. Chatwoot's GET /conversations does not document a
    server-side `updated_within` filter, and earlier review feedback flagged
    that passing it as a query param was a silent no-op. We post-filter
    here so the digest stays bounded by time even on long-lived accounts.
    """
    since_ts: float = 0.0
    if since_iso:
        try:
            # Accept both `2026-06-03T07:00:00Z` and `+00:00` shapes.
            from datetime import datetime as _dt
            since_ts = _dt.fromisoformat(
                since_iso.replace("Z", "+00:00")
            ).timestamp()
        except (ValueError, TypeError):
            since_ts = 0.0

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            _acct_url("/conversations"),
            headers=_headers(),
            params={"status": "snoozed", "labels": "spam"},
        )
        if r.status_code >= 300:
            print(
                f"[chatwoot] search_snoozed_spam non-200 [{r.status_code}]: "
                f"{r.text[:200]}"
            )
            return []
        body = r.json()
        payload = body.get("data") or body
        if isinstance(payload, dict):
            payload = payload.get("payload") or []

        out = []
        for c in payload:
            labels = {(l or "").lower() for l in (c.get("labels") or [])}
            if "spam" not in labels:
                continue
            if since_ts:
                # last_activity_at is a unix epoch number in Chatwoot
                last_ts = c.get("last_activity_at") or 0
                try:
                    last_ts = float(last_ts)
                except (TypeError, ValueError):
                    last_ts = 0
                if last_ts < since_ts:
                    continue
            out.append(c)
        return out


# ── Zoho-ticket surfacing helpers (used by the bridge to make Zoho Desk
#    tickets visible in the Chatwoot dashboard after creation) ────────────
async def post_private_note(conversation_id: int, content: str) -> dict:
    """Add a private (agent-only) note to the conversation.
    Used to surface Zoho ticket creation as a visible bubble in the chat thread."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            _conv_url(conversation_id, "/messages"),
            headers=_headers(),
            json={"content": content, "message_type": "outgoing", "private": True},
        )
        if r.status_code >= 300:
            raise RuntimeError(
                f"Chatwoot post_private_note failed [{r.status_code}]: {r.text}"
            )
        return r.json()


async def send_outgoing_message(conversation_id: int,
                                content: str,
                                to_emails:  Optional[str] = None,
                                cc_emails:  Optional[str] = None,
                                bcc_emails: Optional[str] = None) -> dict:
    """Send a real customer-facing outgoing message on the conversation.
    Uses Chatwoot's existing outbound email channel — no new SMTP creds
    needed.

    All three address fields are COMMA-SEPARATED STRINGS (not arrays);
    Chatwoot's MessageBuilder rejects arrays with a cryptic
    `undefined method 'gsub' for an instance of Array` 422. Pass None
    (or empty) to omit a field.

    `to_emails` is the key field for forwarding: when set, the email
    goes there instead of the conversation's contact. The conversation
    stays linked to the original contact for audit but the actual
    recipient is overridden. This is how the action layer routes a
    forward-category email to a department address without spinning up
    a new conversation."""
    payload: dict = {
        "content":      content,
        "message_type": "outgoing",
        "private":      False,
    }
    if to_emails:  payload["to_emails"]  = to_emails
    if cc_emails:  payload["cc_emails"]  = cc_emails
    if bcc_emails: payload["bcc_emails"] = bcc_emails

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            _conv_url(conversation_id, "/messages"),
            headers=_headers(),
            json=payload,
        )
        if r.status_code >= 300:
            raise RuntimeError(
                f"Chatwoot send_outgoing_message failed [{r.status_code}]: {r.text}"
            )
        return r.json()


async def get_conversation(conversation_id: int) -> dict:
    """Fetch the full conversation JSON. Caller usually only needs
    `custom_attributes`, but returning the whole payload keeps the helper
    reusable. Raises on non-2xx — callers should wrap if they want
    best-effort semantics."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(_conv_url(conversation_id), headers=_headers())
        if r.status_code >= 300:
            raise RuntimeError(
                f"Chatwoot get conversation failed [{r.status_code}]: {r.text}"
            )
        return r.json() or {}


async def get_conversation_messages_raw(conversation_id: int) -> list[dict]:
    """Like get_conversation_messages but RETAINS private notes — needed by
    the template-suggestion dedup check (the card is a private note, so the
    filtered helper hides it from the dedup logic). Oldest-first. Returns []
    on any failure."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(_conv_url(conversation_id, "/messages"),
                                  headers=_headers())
            if r.status_code >= 300:
                return []
            body = r.json() or {}
            payload = body.get("payload")
            if payload is None:
                payload = (body.get("data") or {}).get("payload") or []
            return payload or []
    except Exception as e:  # noqa: BLE001
        print(f"[chatwoot] get messages (raw) error for conv {conversation_id}: {e}")
        return []


async def get_conversation_messages(conversation_id: int) -> list[dict]:
    """Fetch the full message list for a conversation, oldest-first.

    The webhook payloads (especially conversation_status_changed) carry only
    a sparse `messages` array — often just the single message that triggered
    the event — so a ticket built from the payload alone reflects the bot's
    handoff line, not the customer's actual problem. This pulls the real
    transcript from the API instead.

    Returns [] on any failure (best-effort — callers fall back to whatever
    the payload had)."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(_conv_url(conversation_id, "/messages"),
                                  headers=_headers())
            if r.status_code >= 300:
                print(f"[chatwoot] get messages failed [{r.status_code}] "
                      f"for conv {conversation_id}")
                return []
            body = r.json() or {}
            # The endpoint returns {payload: [...]} (sometimes {data: {payload}}).
            payload = body.get("payload")
            if payload is None:
                payload = (body.get("data") or {}).get("payload") or []
            if not payload:
                return []
            # Chatwoot returns the payload CHRONOLOGICALLY (oldest-first) —
            # MessageFinder's default branch is `reorder(created_at desc)
            # .limit(20).reverse`, i.e. ascending. So DON'T reverse it.
            #
            # Drop noise the default endpoint includes: private notes (the
            # bridge's own "🎫 ticket created" notes etc.) and activity rows
            # (message_type 2: "Assigned to … by Zoho Bridge"). Keep only
            # real customer/agent messages (incoming 0 / outgoing 1) so the
            # transcript and the summary aren't polluted.
            return [
                m for m in payload
                if not m.get("private")
                and m.get("message_type") in (0, 1, "incoming", "outgoing")
            ]
    except Exception as e:  # noqa: BLE001
        print(f"[chatwoot] get messages error for conv {conversation_id}: {e}")
        return []


async def merge_custom_attributes(conversation_id: int, attrs: dict) -> dict:
    """Merge keys into the conversation's `custom_attributes` JSONB column via
    Chatwoot's dedicated endpoint. Read-modify-write — concurrency caveat:
    not atomic. Safe under the current single-bridge architecture (FastAPI
    handles webhooks serially per event); revisit if scaled out."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(_conv_url(conversation_id), headers=_headers())
        if r.status_code >= 300:
            raise RuntimeError(
                f"Chatwoot get conversation failed [{r.status_code}]: {r.text}"
            )
        current = (r.json() or {}).get("custom_attributes") or {}
        merged = {**current, **attrs}
        r2 = await client.post(
            _conv_url(conversation_id, "/custom_attributes"),
            headers=_headers(),
            json={"custom_attributes": merged},
        )
        if r2.status_code >= 300:
            raise RuntimeError(
                f"Chatwoot post custom_attributes failed [{r2.status_code}]: {r2.text}"
            )
        return r2.json()


