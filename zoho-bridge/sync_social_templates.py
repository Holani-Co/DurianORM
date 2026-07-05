#!/usr/bin/env python3
# Sync the approved social reply templates (social_templates.yaml — the
# single source of truth, wording from the client's response sheets) into
# Chatwoot as canned responses.
#
# Replaces the old setup_social_templates.py (which had the wording hardcoded
# and could only CREATE). This script:
#   - creates canned responses that don't exist yet
#   - with --update, also overwrites the content of existing short_codes so
#     a wording change in the YAML reaches Chatwoot (UI edits are lost for
#     the codes being updated — the YAML wins; edit the YAML, not the UI,
#     or re-export UI edits back into the YAML first)
#
# The same YAML also feeds review_reply.py: each template's `use_when` +
# `triggers` guide the AI's template pick, so keeping wording and guidance in
# one file keeps them in sync.
#
# Run from the zoho-bridge dir with the venv active:
#   python sync_social_templates.py            # create missing only
#   python sync_social_templates.py --update   # also update changed content

import asyncio
import sys

import httpx
import yaml

import config


def _headers():
    return {"api_access_token": config.CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def _url(suffix):
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


async def main():
    update = "--update" in sys.argv

    with open("social_templates.yaml", "r", encoding="utf-8") as f:
        templates = (yaml.safe_load(f) or {}).get("templates") or []
    if not templates:
        raise SystemExit("social_templates.yaml has no templates")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_url("/canned_responses"), headers=_headers())
        r.raise_for_status()
        existing = {cr["short_code"]: cr for cr in r.json()}

        created = updated = unchanged = skipped = failed = 0
        for t in templates:
            code, content = t["short_code"], t["content"].rstrip("\n")
            cur = existing.get(code)
            if cur is None:
                resp = await client.post(
                    _url("/canned_responses"), headers=_headers(),
                    json={"short_code": code, "content": content},
                )
                if resp.status_code >= 300:
                    print(f"  FAILED create {code} [{resp.status_code}]: {resp.text}")
                    failed += 1
                    continue
                print(f"  created: {code}")
                created += 1
            elif (cur.get("content") or "").rstrip("\n") == content:
                unchanged += 1
            elif update:
                resp = await client.patch(
                    _url(f"/canned_responses/{cur['id']}"), headers=_headers(),
                    json={"content": content},
                )
                if resp.status_code >= 300:
                    print(f"  FAILED update {code} [{resp.status_code}]: {resp.text}")
                    failed += 1
                    continue
                print(f"  updated: {code}")
                updated += 1
            else:
                print(f"  skip (differs, no --update): {code}")
                skipped += 1

        print(f"\nDone. created={created} updated={updated} unchanged={unchanged} "
              f"needs_update={skipped} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
