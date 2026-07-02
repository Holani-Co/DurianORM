#!/usr/bin/env python3
# One-off: tag EXISTING Google-review conversations with reply-status labels
# (review-unreplied / review-replied + review-auto-replied|manually-replied) so
# the reviews inbox filter dropdown works on the current backlog immediately.
# Going forward the poller / reply handler apply these automatically.
#
# Classification per conversation (in the reviews inbox):
#   - has a PUBLIC outgoing msg marked google_auto_reply → replied + auto-replied
#   - has any other PUBLIC outgoing msg                   → replied + manually-replied
#   - otherwise (only the incoming review + private card) → unreplied
#
# Safe + idempotent: add_label merges, so re-running does nothing harmful.
#
# Run from zoho-bridge with the venv + env loaded:
#   venv/bin/python backfill_review_reply_labels.py

import asyncio
import httpx

import config
import reviews_poller as rp

AUTO_SOURCE = rp.AUTO_MARKER["source"]
_H = {"api_access_token": config.CHATWOOT_API_TOKEN}
_BASE = config.CHATWOOT_BASE_URL
_ACCT = config.CHATWOOT_ACCOUNT_ID


def _is_outgoing_public(m: dict) -> bool:
    return m.get("message_type") in (1, "outgoing") and not m.get("private")


async def _conversations_in_reviews_inbox(client: httpx.AsyncClient):
    """Yield every conversation in the reviews inbox, across statuses/pages."""
    for status in ("open", "resolved", "snoozed"):
        page = 1
        while True:
            r = await client.get(
                f"{_BASE}/api/v1/accounts/{_ACCT}/conversations",
                headers=_H,
                params={"inbox_id": config.REVIEWS_INBOX_ID, "status": status,
                        "assignee_type": "all", "page": page},
            )
            r.raise_for_status()
            payload = ((r.json().get("data") or {}).get("payload")) or []
            if not payload:
                break
            for conv in payload:
                yield conv
            page += 1


async def _labels_for(client: httpx.AsyncClient,
                      conv_id: int) -> tuple[list[str], list[str]]:
    """Return (labels_to_add, labels_to_remove) for a review conversation.

    labels_to_remove covers migrating OLD `replied-by-<numeric_id>` labels to
    the new `replied-by-<slug>` naming — we identify the replier's sender and
    remove their id-based label if it existed."""
    r = await client.get(
        f"{_BASE}/api/v1/accounts/{_ACCT}/conversations/{conv_id}/messages",
        headers=_H,
    )
    r.raise_for_status()
    msgs = (r.json().get("payload")) or []
    outgoing = [m for m in msgs if _is_outgoing_public(m)]
    if any((m.get("content_attributes") or {}).get("source") == AUTO_SOURCE
           for m in outgoing):
        return [rp.LBL_REPLIED, rp.LBL_AUTO_REPLIED], []
    if outgoing:
        add = [rp.LBL_REPLIED, rp.LBL_MANUALLY_REPLIED]
        remove: list[str] = []
        # Latest public outgoing carries the replying agent. Use name-slug for
        # the new label; queue the old id-based label for removal.
        for m in reversed(outgoing):
            sender = m.get("sender") or {}
            sid = sender.get("id")
            slug = rp.agent_name_slug(
                sender.get("available_name") or sender.get("name") or "",
                sender.get("email") or "",
                sid,
            )
            if slug:
                add.append(f"replied-by-{slug}")
            if sid and slug != str(sid):
                remove.append(f"replied-by-{sid}")
            break
        return add, remove
    return [rp.LBL_UNREPLIED], []


async def main():
    counts = {rp.LBL_UNREPLIED: 0, rp.LBL_AUTO_REPLIED: 0, rp.LBL_MANUALLY_REPLIED: 0}
    removed = 0
    async with httpx.AsyncClient(timeout=30) as client:
        async for conv in _conversations_in_reviews_inbox(client):
            conv_id = conv.get("id")
            if not conv_id:
                continue
            try:
                add, remove = await _labels_for(client, conv_id)
                await rp.tag_reply_status(conv_id, *add, remove=tuple(remove))
                key = next((l for l in add if l != rp.LBL_REPLIED), add[0])
                counts[key] = counts.get(key, 0) + 1
                removed += len(remove)
                extra = f"  (removed {', '.join(remove)})" if remove else ""
                print(f"  #{conv_id} → {', '.join(add)}{extra}")
            except Exception as e:
                print(f"  #{conv_id} FAILED: {e}")
    print("\nDone. "
          f"unreplied={counts[rp.LBL_UNREPLIED]}  "
          f"auto={counts[rp.LBL_AUTO_REPLIED]}  "
          f"manual={counts[rp.LBL_MANUALLY_REPLIED]}  "
          f"old_id_labels_cleaned={removed}")


if __name__ == "__main__":
    asyncio.run(main())
