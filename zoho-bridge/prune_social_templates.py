#!/usr/bin/env python3
# Delete orphaned social canned responses from Chatwoot — any `social_*`
# canned response whose short_code is no longer in social_templates.yaml.
#
# Needed after the platform/surface split (legacy social_* → instagram_dm_* /
# instagram_comment_* / facebook_dm_* / facebook_comment_*): sync_social_templates.py
# CREATES the new codes but leaves the OLD ones behind as orphans. This removes
# exactly those, keeping the canned-response page clean (four platform×surface
# blocks).
#
# SAFE BY DEFAULT: lists what WOULD be deleted and exits. Pass --apply to
# actually delete. Only ever touches short_codes starting with "social_", and
# never one that's still present in the YAML.
#
# Run AFTER sync_social_templates.py --update, from the zoho-bridge dir:
#   python prune_social_templates.py            # dry-run (shows orphans)
#   python prune_social_templates.py --apply    # actually delete them

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
    apply = "--apply" in sys.argv

    with open("social_templates.yaml", "r", encoding="utf-8") as f:
        valid = {t["short_code"] for t in (yaml.safe_load(f) or {}).get("templates") or []}
    if not valid:
        raise SystemExit("social_templates.yaml has no templates — refusing to prune")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_url("/canned_responses"), headers=_headers())
        r.raise_for_status()
        # Managed social prefixes: the current instagram_*/facebook_* families
        # plus the legacy social_* codes (pre-4-way-split) so the migration
        # leaves no orphans behind.
        managed = ("social_", "instagram_", "facebook_")
        orphans = [cr for cr in r.json()
                   if (cr.get("short_code") or "").startswith(managed)
                   and cr["short_code"] not in valid]

        if not orphans:
            print("No orphaned social_* canned responses. Nothing to prune.")
            return

        print(f"{'DELETING' if apply else 'WOULD DELETE'} {len(orphans)} orphaned "
              f"canned response(s):")
        for cr in orphans:
            print(f"  - {cr['short_code']}")

        if not apply:
            print("\nDry-run only. Re-run with --apply to delete them.")
            return

        deleted = failed = 0
        for cr in orphans:
            resp = await client.delete(
                _url(f"/canned_responses/{cr['id']}"), headers=_headers())
            if resp.status_code >= 300:
                print(f"  FAILED delete {cr['short_code']} [{resp.status_code}]: {resp.text}")
                failed += 1
            else:
                deleted += 1
        print(f"\nDone. deleted={deleted} failed={failed}")


if __name__ == "__main__":
    asyncio.run(main())
