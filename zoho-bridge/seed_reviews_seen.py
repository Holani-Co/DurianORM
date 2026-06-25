#!/usr/bin/env python3
# One-time: mark every review that currently exists on every Durian GBP
# location as "seen" in the bridge's state DB, WITHOUT creating a Chatwoot
# conversation for any of them. After running this, the next poll cycle
# only ingests reviews that are genuinely new from this moment forward.
#
# Use when you don't want the historical backlog flooding the Chatwoot
# inbox (e.g. ~1200 historical reviews × dozens of locations).
#
# Idempotent — safe to re-run. Doesn't touch Chatwoot at all.
#
# Run from the zoho-bridge dir with the venv active:
#   python seed_reviews_seen.py

import asyncio

import config
import google_reviews as gr
import reviews_state as state


async def main():
    if not config.GOOGLE_REVIEWS_ENABLED:
        print("GOOGLE_REVIEWS_ENABLED is false — nothing to seed.")
        return

    state.init()
    print("Discovering locations…")
    account_ids = ([config.GBP_ACCOUNT_ID] if config.GBP_ACCOUNT_ID
                   else await gr.list_account_ids())

    total_marked = 0
    total_already = 0
    for acct in account_ids:
        acct_num = acct.split("/")[-1]
        acct_path = acct if acct.startswith("accounts/") else f"accounts/{acct_num}"
        for loc in await gr.list_locations(acct_path):
            try:
                reviews = await gr.list_reviews(acct_num, loc["id"])
            except Exception as e:
                print(f"  ! skip {loc['title']}: {e}")
                continue
            marked = 0
            already = 0
            for rv in reviews:
                if state.is_seen(rv["review_id"]):
                    already += 1
                    continue
                # conversation_id=0 marker = "seen but never ingested" — the
                # poller's is_seen() check only cares that the row exists.
                state.mark_seen(rv["review_id"], 0, rv["reply_path"],
                                rv["stars"] or 0, replied=bool(rv["has_reply"]))
                marked += 1
            total_marked += marked
            total_already += already
            print(f"  {loc['title']}: {marked} marked seen, {already} already known")

    print(f"\nDone. {total_marked} reviews marked seen (skipped ingest); "
          f"{total_already} already in state. The poller will now only "
          f"ingest reviews newer than these.")


if __name__ == "__main__":
    asyncio.run(main())
