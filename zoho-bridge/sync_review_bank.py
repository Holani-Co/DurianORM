#!/usr/bin/env python3
# Sync the Google-review reply bank (review_reply_bank.yaml — the vertical × case
# × ~10 phrasing variants) into Chatwoot as Canned Responses, so the client can
# SEE and EDIT every reply straight from the UI (Settings → Canned Responses),
# grouped by vertical via the short_code prefix.
#
# short_code scheme:  review_<vertical>_<case>_<NN>
#   e.g. review_furniture_positive_staff_01, review_doors_negative_quality_03
# The vertical is the first token after `review_`, so the UI list groups/searches
# cleanly by furniture / fhc / doors. NN is zero-padded so variants sort 01..10.
#
# The reply drafter (review_reply.draft_review) reads these SAME canned responses
# at reply time — UI edits win, the YAML is the seed/fallback — so editing a body
# here immediately changes what the AI drafts. Mirrors setup_review_templates.py
# / sync_showroom_templates.py deliberately: one sync idiom in this repo.
#
# NAMESPACE GUARD: only ever touches short_codes matching
# review_(furniture|fhc|doors)_*. The legacy flat review_* templates
# (review_positive_5star, …) and the hand-tuned social_* templates are never
# clobbered by a re-sync from this file.
#
#   python sync_review_bank.py            # create missing only (safe, default)
#   python sync_review_bank.py --update   # also overwrite changed bodies
#   python sync_review_bank.py --dry-run  # show what would change, touch nothing

import asyncio
import sys
from pathlib import Path

import httpx
import yaml

import config

VERTICALS = ("furniture", "fhc", "doors")
_BANK_PATH = Path(__file__).parent / "review_reply_bank.yaml"


def _headers():
    return {"api_access_token": config.CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def _url(suffix):
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


def _load_bank_entries() -> dict:
    """Flatten the YAML bank into {short_code: content}. Only known verticals are
    emitted so a stray key in the sheet can't create off-namespace responses."""
    with open(_BANK_PATH, encoding="utf-8") as f:
        verticals = (yaml.safe_load(f) or {}).get("verticals") or {}
    entries = {}
    for vert, vdata in verticals.items():
        if vert not in VERTICALS:
            print(f"  skip unknown vertical: {vert}")
            continue
        for case, cdata in ((vdata or {}).get("cases") or {}).items():
            options = (cdata or {}).get("options") or []
            for i, body in enumerate(options, start=1):
                sc = f"review_{vert}_{case}_{i:02d}"
                entries[sc] = (body or "").strip()
    return entries


async def main():
    update = "--update" in sys.argv
    dry_run = "--dry-run" in sys.argv

    entries = _load_bank_entries()
    if not entries:
        raise SystemExit("review_reply_bank.yaml produced no templates")
    print(f"Loaded {len(entries)} reply-bank variants from {_BANK_PATH.name}"
          f"{'  [DRY RUN]' if dry_run else ''}")

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(_url("/canned_responses"), headers=_headers())
        r.raise_for_status()
        existing = {cr["short_code"]: cr for cr in r.json()}

        created = updated = skipped = failed = 0
        for short_code, content in sorted(entries.items()):
            cur = existing.get(short_code)
            if cur is None:
                if dry_run:
                    print(f"  would create: {short_code}")
                    created += 1
                    continue
                resp = await client.post(
                    _url("/canned_responses"), headers=_headers(),
                    json={"short_code": short_code, "content": content})
                if resp.status_code >= 300:
                    print(f"  FAILED create {short_code} [{resp.status_code}]: {resp.text}")
                    failed += 1
                    continue
                print(f"  created: {short_code}")
                created += 1
            elif update and (cur.get("content") or "").strip() != content:
                if dry_run:
                    print(f"  would update: {short_code}")
                    updated += 1
                    continue
                resp = await client.patch(
                    _url(f"/canned_responses/{cur['id']}"), headers=_headers(),
                    json={"short_code": short_code, "content": content})
                if resp.status_code >= 300:
                    print(f"  FAILED update {short_code} [{resp.status_code}]: {resp.text}")
                    failed += 1
                    continue
                print(f"  updated: {short_code}")
                updated += 1
            else:
                skipped += 1

    print(f"\nDone. created={created} updated={updated} skipped={skipped} failed={failed}"
          f"{'  (dry run — nothing changed)' if dry_run else ''}")
    if not update and any(sc in existing for sc in entries):
        print("Note: existing bodies left untouched. Re-run with --update to overwrite them.")


if __name__ == "__main__":
    asyncio.run(main())
