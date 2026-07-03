#!/usr/bin/env python3
# One-time backfill (Google side): dump every review's ACTUAL posting date so
# existing Chatwoot review conversations — ingested before the poller started
# storing `review_created_at` — can be filtered/sorted by real review date.
#
# This script only reads Google and writes a JSON map {review_path: create_time}.
# A companion Rails runner writes it onto the conversations' additional_attributes
# (there is no Chatwoot API to PATCH additional_attributes, so the write happens
# via ActiveRecord on the Rails side).
#
#   # from the zoho-bridge dir, venv active:
#   python backfill_review_dates.py                 # → review_dates.json
#   python backfill_review_dates.py /tmp/dates.json # custom output path
#
# Then on the Rails side (chatwoot dir):
#   bundle exec rails runner scripts/backfill_review_dates.rb \
#       ../zoho-bridge/review_dates.json

import asyncio
import json
import sys

import httpx

import google_reviews as gr
from reviews_poller import _discover_locations


async def _all_reviews(account_id: str, location_id: str) -> list[tuple[str, str]]:
    """Every (reply_path, create_time) for a location, following nextPageToken."""
    url = f"{gr.REVIEWS_V4}/accounts/{account_id}/locations/{location_id}/reviews"
    out: list[tuple[str, str]] = []
    token: str | None = None
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            params = {"pageSize": 50, "orderBy": "updateTime desc"}
            if token:
                params["pageToken"] = token
            data = await gr._get(client, url, params)
            for rv in data.get("reviews", []):
                rid = rv.get("reviewId") or rv.get("name", "").split("/")[-1]
                ct = rv.get("createTime", "")
                if ct:
                    path = f"accounts/{account_id}/locations/{location_id}/reviews/{rid}"
                    out.append((path, ct))
            token = data.get("nextPageToken")
            if not token:
                break
    return out


async def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "review_dates.json"
    mapping: dict[str, str] = {}
    for loc in await _discover_locations():
        pairs = await _all_reviews(loc["account_id"], loc["location_id"])
        for path, ct in pairs:
            mapping[path] = ct
        print(f"[backfill] {loc['title']}: {len(pairs)} reviews")
    with open(out_path, "w") as f:
        json.dump(mapping, f, indent=2)
    print(f"[backfill] wrote {len(mapping)} review dates → {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
