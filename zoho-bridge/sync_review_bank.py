#!/usr/bin/env python3
# Sync the Google-review reply bank (review_reply_bank.yaml — the vertical × case
# × ~10 phrasing variants) into Chatwoot as Canned Responses, so the client can
# SEE and EDIT every reply straight from the UI (Settings → Canned Responses),
# grouped by vertical via the short_code prefix.
#
# short_code scheme:  review_<vertical>_<case>_<NN>
#   e.g. review_furniture_positive_staff_01, review_doors_negative_quality_03
# The `review` prefix (first token before the underscore) files every entry
# under the "Google Reviews" tab of the Canned Responses UI (templateTaxonomy.js
# keys tabs on that prefix). Vertical is the next token, so the list also
# groups/searches by furniture / fhc / doors. NN is zero-padded so variants sort
# 01..10.
#
# The reply drafter (review_reply.draft_review) reads these SAME canned responses
# at reply time — UI edits win, the YAML is the seed/fallback — so editing a body
# here immediately changes what the AI drafts. Both review draft paths (first
# ingest AND edit/regenerate) go through draft_review, so nothing else consumes
# bare `review_` codes; the bank owns that namespace.
#
# --prune removes stragglers under the review namespace that are NOT current bank
# entries: the orphaned `reviewbank_*` codes from the earlier naming, and the
# retired 8 flat templates (review_positive_5star, …). social_* and other codes
# are never touched.
#
#   python sync_review_bank.py            # create missing only (safe, default)
#   python sync_review_bank.py --update   # also overwrite changed bodies
#   python sync_review_bank.py --prune    # delete orphaned reviewbank_* + old 8
#   python sync_review_bank.py --dry-run  # show what would change, touch nothing
#   (flags combine, e.g. --update --prune)

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


def _is_prunable(short_code: str, current: set) -> bool:
    """A canned response to remove under --prune: an orphaned `reviewbank_*`
    code, or a `review_*` code that is not a current bank entry (the retired
    flat 8). Never matches social_* or unrelated codes."""
    if short_code in current:
        return False
    return short_code.startswith("reviewbank_") or short_code.startswith("review_")


async def main():
    update = "--update" in sys.argv
    dry_run = "--dry-run" in sys.argv
    prune = "--prune" in sys.argv

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

        pruned = 0
        if prune:
            targets = sorted(sc for sc in existing if _is_prunable(sc, set(entries)))
            for sc in targets:
                if dry_run:
                    print(f"  would prune: {sc}")
                    pruned += 1
                    continue
                resp = await client.delete(
                    _url(f"/canned_responses/{existing[sc]['id']}"), headers=_headers())
                if resp.status_code >= 300:
                    print(f"  FAILED prune {sc} [{resp.status_code}]: {resp.text}")
                    failed += 1
                    continue
                print(f"  pruned: {sc}")
                pruned += 1

    print(f"\nDone. created={created} updated={updated} pruned={pruned} "
          f"skipped={skipped} failed={failed}"
          f"{'  (dry run — nothing changed)' if dry_run else ''}")
    if not update and any(sc in existing for sc in entries):
        print("Note: existing bodies left untouched. Re-run with --update to overwrite them.")
    if not prune and any(_is_prunable(sc, set(entries)) for sc in existing):
        print("Note: orphaned reviewbank_*/old review_* codes remain. Re-run with --prune to remove.")


if __name__ == "__main__":
    asyncio.run(main())
