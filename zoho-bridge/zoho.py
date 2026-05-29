# Zoho Desk client: OAuth token cache + ticket creation.

import time
import httpx

import config

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

    first_msg = next((m.get("content", "") for m in messages if m.get("content")), "")
    subject   = f"[{inbox}] {first_msg[:60] or 'New conversation'}"

    # Include Chatwoot team in description for context (e.g., "Legal" → Zoho agent
    # knows what kind of ticket this is even before routing inside Zoho)
    team_meta  = (conv.get("meta") or {}).get("team")
    team_label = f"\n\n— Chatwoot team: {team_meta.get('name')}" if team_meta else ""

    return {
        "subject":      subject,
        "description":  transcript + team_label,
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


async def create_ticket(payload: dict) -> dict:
    body = _build_ticket_body(payload)

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
