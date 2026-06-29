#!/usr/bin/env python3
# One-time (re-runnable) backfill: apply store + star labels — and create the
# matching Label records — on EXISTING Google Reviews conversations, so the
# reviews inbox's Store / Rating dropdowns immediately list every store and
# rating already present in the data (new reviews get these labels live via
# reviews_poller).
#
# Why it's needed: the store dropdown is populated from `store-<slug>` Label
# records, and the list filter matches the `store-<slug>` tagging on each
# conversation — neither exists for reviews ingested before this feature.
#
# Safe to re-run: add_label merges (never clobbers) and ensure_label is
# idempotent, so a second run is a no-op for already-labelled conversations.
#
#   python backfill_review_labels.py             # apply labels
#   python backfill_review_labels.py --dry-run   # report only, write nothing
#
# Reads each conversation's additional_attributes.location (→ store label) and
# .stars (→ review-<n>star). A missing/invalid rating is left as-is so an
# existing star tagging is never overwritten with "unrated".

import asyncio
import sys

import httpx

import config
import chatwoot
from reviews_poller import _store_label, _ensure_label_once


async def _list_review_conversations():
    """Yield every conversation in the reviews inbox, paging until exhausted.
    Guards client-side on inbox_id in case the server ignores the filter."""
    page = 1
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            r = await client.get(
                f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/"
                f"{config.CHATWOOT_ACCOUNT_ID}/conversations",
                headers=chatwoot._headers(),
                params={"inbox_id": config.REVIEWS_INBOX_ID,
                        "status": "all", "page": page},
            )
            r.raise_for_status()
            data = r.json().get("data") or {}
            payload = data.get("payload") or []
            if not payload:
                break
            for conv in payload:
                if conv.get("inbox_id") == config.REVIEWS_INBOX_ID:
                    yield conv
            page += 1


def _labels_for(conv: dict) -> list[str]:
    aa = conv.get("additional_attributes") or {}
    out = []
    location = (aa.get("location") or "").strip()
    if location:
        out.append(_store_label(location))
    try:
        stars = int(aa.get("stars"))
    except (TypeError, ValueError):
        stars = 0
    if 1 <= stars <= 5:
        out.append(f"review-{stars}star")
    return out


async def main():
    dry = "--dry-run" in sys.argv
    scanned = updated = 0
    async for conv in _list_review_conversations():
        scanned += 1
        cid = conv.get("id")
        existing = {(label or "").lower() for label in (conv.get("labels") or [])}
        missing = [l for l in _labels_for(conv) if l.lower() not in existing]
        if not missing:
            continue
        print(f"conv {cid}: + {missing}" + ("  (dry-run)" if dry else ""))
        updated += 1
        if dry:
            continue
        for label in missing:
            await _ensure_label_once(label)
            try:
                await chatwoot.add_label(cid, label)
            except Exception as e:
                print(f"  add_label({label}) failed for conv {cid}: {e}")
        if scanned % 100 == 0:
            print(f"  …scanned {scanned}")
    verb = "would update" if dry else "updated"
    print(f"\nScanned {scanned} review conversations; {verb} {updated}.")


if __name__ == "__main__":
    if not config.REVIEWS_INBOX_ID:
        raise SystemExit("REVIEWS_INBOX_ID not set in .env")
    asyncio.run(main())
