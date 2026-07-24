#!/usr/bin/env python3
# Sync the city showroom templates (showroom_templates.yaml — generated from the
# client's "City wise Location template" sheet) into Chatwoot as canned
# responses, so agents can insert them by hand and the retail gate can send them
# by short_code.
#
# Mirrors sync_social_templates.py deliberately — same flags, same output — so
# there is one sync idiom in this repo, not two.
#
#   python sync_showroom_templates.py            # create missing only
#   python sync_showroom_templates.py --update   # also overwrite changed content
#   python sync_showroom_templates.py --dry-run  # show what would change
#
# NAMESPACE GUARD: this script only ever touches short_codes starting with
# `showroom_`. The social templates the client has tuned by hand live under
# `social_*` and cannot be clobbered by a re-sync from this file.

import asyncio
import sys

import httpx
import yaml

import config

PREFIX = "showroom_"


def _headers():
    return {"api_access_token": config.CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def _url(suffix):
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


async def main():
    update = "--update" in sys.argv
    dry = "--dry-run" in sys.argv

    with open("showroom_templates.yaml", "r", encoding="utf-8") as f:
        templates = (yaml.safe_load(f) or {}).get("templates") or []
    if not templates:
        raise SystemExit("showroom_templates.yaml has no templates")

    bad = [t["short_code"] for t in templates
           if not str(t.get("short_code", "")).startswith(PREFIX)]
    if bad:
        raise SystemExit(f"refusing to sync — short_codes outside '{PREFIX}': {bad}")

    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_url("/canned_responses"), headers=_headers())
        r.raise_for_status()
        existing = {cr["short_code"]: cr for cr in r.json()}

        created = updated = unchanged = skipped = failed = 0
        for t in templates:
            code, content = t["short_code"], (t.get("content") or "").rstrip("\n")
            if not content:
                print(f"  skip (empty content): {code}")
                skipped += 1
                continue
            cur = existing.get(code)
            if cur is None:
                if dry:
                    print(f"  would create: {code}")
                    created += 1
                    continue
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
                if dry:
                    print(f"  would update: {code}")
                    updated += 1
                    continue
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
