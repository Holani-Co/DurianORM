#!/usr/bin/env python3
# One-time helper: create the "Google Reviews" API-channel inbox in Chatwoot
# and print its inbox_id (put that into REVIEWS_INBOX_ID in .env).
#
# Run from the zoho-bridge dir with the venv active:
#   python setup_google_reviews.py
#
# Safe to re-run: if an inbox with the same name exists, it just prints the id.

import asyncio
import httpx
import config

INBOX_NAME = "Google Reviews"


def _headers():
    return {"api_access_token": config.CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def _url(suffix):
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


async def main():
    async with httpx.AsyncClient(timeout=15) as client:
        # Already exists?
        r = await client.get(_url("/inboxes"), headers=_headers())
        r.raise_for_status()
        for ib in r.json().get("payload", []):
            if ib.get("name") == INBOX_NAME:
                print(f"Inbox already exists. REVIEWS_INBOX_ID={ib['id']}")
                return
        # Create an API channel inbox. webhook_url left blank: we use the
        # account-level webhook already registered for this bridge.
        r = await client.post(
            _url("/inboxes"),
            headers=_headers(),
            json={"name": INBOX_NAME, "channel": {"type": "api", "webhook_url": ""}},
        )
        if r.status_code >= 300:
            raise SystemExit(f"Create inbox failed [{r.status_code}]: {r.text}")
        inbox = r.json()
        print(f"Created '{INBOX_NAME}'. Put this in your .env:")
        print(f"REVIEWS_INBOX_ID={inbox.get('id')}")


if __name__ == "__main__":
    asyncio.run(main())
