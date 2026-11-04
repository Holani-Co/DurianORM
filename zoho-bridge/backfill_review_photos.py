#!/usr/bin/env python3
# One-time (re-runnable) backfill: attach the customer's photos to Google
# reviews ALREADY in the ORM.
#
# Why it's needed: before review photos were supported the poller copied only a
# review's text and rating, so photos a customer added on Google never reached
# the conversation (conv 12052). New reviews now carry them on ingest.
#
# Walks each location's 50 newest reviews (the poller's own window). For every
# review with photos whose conversation doesn't have them yet, posts them as a
# PRIVATE note — a note never reopens the conversation and is never posted to
# Google. Marks the conversation `review_photos` so a re-run skips it.
#
#   venv/bin/python backfill_review_photos.py            # list only, changes nothing
#   venv/bin/python backfill_review_photos.py --apply    # attach the photos
#
# The Google credentials live in the zoho-bridge systemd unit, not .env — run on
# the VM, where this script reads them from the unit itself.

import os
import shlex
import subprocess

if not os.environ.get("GOOGLE_REFRESH_TOKEN"):
    _unit_env = subprocess.run(
        ["systemctl", "show", "-p", "Environment", "--value", "zoho-bridge"],
        capture_output=True, text=True).stdout
    for _kv in shlex.split(_unit_env):
        _k, _, _v = _kv.partition("=")
        os.environ.setdefault(_k, _v)

import asyncio
import sys

import chatwoot
import config
import google_reviews as gr
import reviews_poller
import reviews_state as state


async def main(apply: bool) -> None:
    found = done = 0
    for loc in await reviews_poller._discover_locations():
        try:
            reviews = await gr.list_reviews(loc["account_id"], loc["location_id"])
        except Exception as e:
            print(f"{loc['title']}: list failed — {str(e)[:80]}")
            continue
        for rv in reviews:
            if not rv["photos"]:
                continue
            conv_id = (state.seen_record(rv["review_id"]) or {}).get("conversation_id")
            if not conv_id:
                continue
            try:
                conv = await chatwoot.get_conversation(conv_id)
            except Exception as e:
                print(f"conv {conv_id}: could not read — {str(e)[:80]}")
                continue
            if "review_photos" in (conv.get("custom_attributes") or {}):
                continue
            found += 1
            print(f"conv #{conv.get('display_id') or conv_id} · {rv['stars']}★ "
                  f"{rv['reviewer']} @ {loc['title']} · {len(rv['photos'])} photo(s)")
            if not apply:
                continue
            photos = await gr.download_review_photos(rv["photos"])
            if not photos:
                print("    ✗ none could be downloaded")
                continue
            await chatwoot.create_message_with_files(
                conv_id, f"📷 Photos the customer added to this review on Google "
                         f"({len(photos)})",
                photos, message_type="outgoing", private=True)
            await chatwoot.merge_custom_attributes(conv_id, {"review_photos": len(photos)})
            done += 1
            print(f"    ✓ attached {len(photos)}")
    if apply:
        print(f"\nAttached photos to {done} of {found} conversation(s).")
    else:
        print(f"\n{found} conversation(s) to update. List only — nothing changed. "
              f"Re-run with --apply to attach the photos.")


if __name__ == "__main__":
    if not config.REVIEWS_INBOX_ID:
        raise SystemExit("REVIEWS_INBOX_ID not set")
    asyncio.run(main(apply="--apply" in sys.argv[1:]))
