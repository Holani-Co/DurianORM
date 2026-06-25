#!/usr/bin/env python3
# One-time: mark historical Google reviews as "seen" in the bridge's state DB
# WITHOUT creating Chatwoot conversations for them. After running, the poller
# only ingests reviews newer than the cutoff.
#
# USAGE
#   python seed_reviews_seen.py                  # mark EVERY existing review (no cutoff)
#   python seed_reviews_seen.py 2025-05-25       # mark only reviews older than this date
#   python seed_reviews_seen.py last-month       # mark only reviews older than 30 days ago
#
# After: re-enable the poller and only reviews from the cutoff onwards flow in.
# Idempotent — safe to re-run. Doesn't touch Chatwoot at all.

import asyncio
import sys
from datetime import datetime, timedelta, timezone

import config
import google_reviews as gr
import reviews_state as state


def _parse_cutoff(arg: str | None):
    """Returns a UTC datetime: reviews created BEFORE this are marked seen and
    skipped; reviews created on/after this fall through to the live poller.
    `None` (no arg) → far-future cutoff → mark everything (legacy behaviour)."""
    if not arg:
        return datetime.now(timezone.utc) + timedelta(days=365)  # mark all
    if arg == "last-month":
        return datetime.now(timezone.utc) - timedelta(days=30)
    try:
        return datetime.fromisoformat(arg).replace(tzinfo=timezone.utc)
    except ValueError:
        raise SystemExit(f"Could not parse cutoff '{arg}' — use YYYY-MM-DD or 'last-month'.")


async def main():
    if not config.GOOGLE_REVIEWS_ENABLED:
        print("GOOGLE_REVIEWS_ENABLED is false — nothing to seed.")
        return

    cutoff = _parse_cutoff(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Cutoff: reviews created BEFORE {cutoff.isoformat()} → marked seen")
    print(f"        reviews created ON/AFTER that → will be ingested on next poll")

    state.init()
    print("Discovering locations…")
    account_ids = ([config.GBP_ACCOUNT_ID] if config.GBP_ACCOUNT_ID
                   else await gr.list_account_ids())

    total_marked = total_skipped = total_already = 0
    for acct in account_ids:
        acct_num = acct.split("/")[-1]
        acct_path = acct if acct.startswith("accounts/") else f"accounts/{acct_num}"
        for loc in await gr.list_locations(acct_path):
            try:
                reviews = await gr.list_reviews(acct_num, loc["id"])
            except Exception as e:
                print(f"  ! skip {loc['title']}: {e}")
                continue
            marked = skipped = already = 0
            for rv in reviews:
                if state.is_seen(rv["review_id"]):
                    already += 1
                    continue
                try:
                    created = datetime.fromisoformat(
                        (rv.get("create_time") or "").replace("Z", "+00:00"))
                except Exception:
                    created = None
                if created and created >= cutoff:
                    # Newer than cutoff — leave it alone so the poller ingests it
                    skipped += 1
                    continue
                state.mark_seen(rv["review_id"], 0, rv["reply_path"],
                                rv["stars"] or 0, replied=bool(rv["has_reply"]))
                marked += 1
            total_marked += marked
            total_skipped += skipped
            total_already += already
            print(f"  {loc['title']}: {marked} marked seen, "
                  f"{skipped} kept for ingestion, {already} already known")

    print(f"\nDone. {total_marked} marked seen, {total_skipped} will be "
          f"ingested by the poller, {total_already} already in state.")


if __name__ == "__main__":
    asyncio.run(main())
