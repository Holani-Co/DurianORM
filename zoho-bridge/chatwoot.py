# Chatwoot Application API client. Add more methods here as needed.
# Docs: https://www.chatwoot.com/developers/api/

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
    """Optional helper for future use (e.g., tag with classified team name)."""
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            _conv_url(conversation_id, "/labels"),
            headers=_headers(),
            json={"labels": [label]},
        )
        if r.status_code >= 300:
            raise RuntimeError(f"Chatwoot add_label failed [{r.status_code}]: {r.text}")
        return r.json()


# ── API-channel helpers (used by the Google Reviews ingest) ────────────────
# An API-channel inbox lets us push arbitrary external messages (reviews) in as
# conversations. Flow: create/find contact → create conversation → add message.

def _acct_url(suffix: str) -> str:
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


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
    /spam-digest endpoint to build the daily review summary."""
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"status": "snoozed", "labels": "spam"}
        if since_iso:
            params["updated_within"] = since_iso
        r = await client.get(
            _acct_url("/conversations"),
            headers=_headers(),
            params=params,
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
        return [
            c for c in payload
            if "spam" in {(l or "").lower() for l in (c.get("labels") or [])}
        ]


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


