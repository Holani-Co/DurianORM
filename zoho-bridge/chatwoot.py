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
