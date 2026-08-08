#!/usr/bin/env python3
"""One-off cleanup for reviews that were mislabeled `review-unreplied` even
though they already carry a reply on Google.

Background: before the has_reply short-circuit in reviews_poller._ingest_review,
an already-answered review fell through to the handoff branch and got tagged
`review-unreplied`. Those conversations were marked seen, so the poller won't
re-touch them. This script fixes them retroactively.

For every conversation in the Google Reviews inbox tagged `review-unreplied`,
it looks up the review on Google. If the review actually has a reply, it swaps
`review-unreplied` → `review-replied` and resolves the conversation. Idempotent
(safe to re-run): it only removes/adds labels and resolves — it never re-posts
to Google or mirrors messages.

Usage:
    ./venv/bin/python cleanup_review_labels.py            # dry run (default)
    ./venv/bin/python cleanup_review_labels.py --apply    # actually change
    ./venv/bin/python cleanup_review_labels.py --apply --limit 50
"""
import argparse
import asyncio

import httpx

import config
import chatwoot
import google_reviews as gr
from reviews_poller import LBL_UNREPLIED, LBL_REPLIED


async def _list_unreplied_convs() -> list[dict]:
    """Page through the reviews inbox for conversations tagged review-unreplied."""
    out: list[dict] = []
    page = 1
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            r = await client.get(
                chatwoot._acct_url("/conversations"),
                headers=chatwoot._headers(),
                params={"inbox_id": config.REVIEWS_INBOX_ID,
                        "labels": LBL_UNREPLIED, "page": page},
            )
            if r.status_code >= 300:
                raise RuntimeError(
                    f"list conversations failed [{r.status_code}]: {r.text[:200]}")
            body = r.json()
            payload = (body.get("data") or body)
            if isinstance(payload, dict):
                payload = payload.get("payload") or []
            if not payload:
                break
            # Server-side label filter can be loose — keep only real matches.
            for c in payload:
                labels = {(l or "").lower() for l in (c.get("labels") or [])}
                if LBL_UNREPLIED in labels:
                    out.append(c)
            page += 1
    return out


async def _review_has_reply(review_path: str) -> tuple[bool, str]:
    """GET a single review; return (has_reply, reviewer_name)."""
    async with httpx.AsyncClient(timeout=20) as client:
        data = await gr._get(client, f"{gr.REVIEWS_V4}/{review_path}")
    reviewer = (data.get("reviewer") or {}).get("displayName", "")
    return bool(data.get("reviewReply")), reviewer


async def main(apply: bool, limit: int) -> None:
    convs = await _list_unreplied_convs()
    print(f"[cleanup] {len(convs)} conversation(s) tagged {LBL_UNREPLIED} "
          f"in the reviews inbox")

    fixed = skipped = errors = 0
    for c in convs:
        if limit and fixed >= limit:
            print(f"[cleanup] hit --limit {limit}; stopping")
            break
        conv_id = c.get("id")
        attrs = c.get("custom_attributes") or {}
        review_path = attrs.get("review_path") or ""
        loc = (c.get("additional_attributes") or {}).get("location", "")
        if not review_path:
            print(f"  · conv {conv_id}: no review_path — skipping (can't verify)")
            skipped += 1
            continue
        try:
            has_reply, reviewer = await _review_has_reply(review_path)
        except Exception as e:
            print(f"  ! conv {conv_id}: Google lookup failed — {e}")
            errors += 1
            continue
        if not has_reply:
            skipped += 1
            continue

        who = reviewer or "?"
        if not apply:
            print(f"  → conv {conv_id} ({who} @ {loc}): would swap "
                  f"{LBL_UNREPLIED}→{LBL_REPLIED} + resolve")
            fixed += 1
            continue
        try:
            await chatwoot.remove_label(conv_id, LBL_UNREPLIED)
            await chatwoot.add_label(conv_id, LBL_REPLIED)
            await chatwoot.toggle_status(conv_id, "resolved")
            print(f"  ✓ conv {conv_id} ({who} @ {loc}): marked replied + resolved")
            fixed += 1
        except Exception as e:
            print(f"  ! conv {conv_id}: update failed — {e}")
            errors += 1

    verb = "fixed" if apply else "would fix"
    print(f"[cleanup] {verb}={fixed}  skipped(no Google reply)={skipped}  "
          f"errors={errors}")
    if not apply:
        print("[cleanup] dry run — re-run with --apply to make changes")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually change labels/status (default: dry run)")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N fixes (0 = no limit)")
    args = ap.parse_args()
    asyncio.run(main(args.apply, args.limit))
