# durian.in website product-reviews poller.
#
# On a timer: pull every website review and for each NEW one create a Chatwoot
# conversation in the Website Reviews inbox (contact = reviewer, incoming
# message = review text + rating). Human-only: an agent replies in Chatwoot and
# the reply is posted back to durian.in by main.handle_website_review_reply.
#
# Deliberately simpler than the Google poller — no locations, no store labels,
# no auto-reply/reply-bank, no low-star email forwarding.

import asyncio
import re

import config
import chatwoot
import website_reviews as wr
import website_reviews_state as state

# content_attributes marker parity with the Google poller: any reply we post
# ourselves (e.g. echoing an existing website reply) is tagged so the outgoing
# webhook doesn't try to re-post it to durian.in.
AUTO_MARKER = {"source": "website_review_echo"}

LBL_REPLIED = "review-replied"
LBL_UNREPLIED = "review-unreplied"

_ensured_labels: set[str] = set()


def _stars_bar(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars) if stars else "(rating unknown)"


def _reviewer_name(rv: dict) -> str:
    uid = rv.get("user_id")
    return f"Website reviewer #{uid}" if uid else "Website reviewer"


async def _ensure_label_once(label: str, show_on_sidebar: bool = True) -> None:
    if label in _ensured_labels:
        return
    try:
        await chatwoot.ensure_label(label, show_on_sidebar=show_on_sidebar)
        _ensured_labels.add(label)
    except Exception as e:
        print(f"[web-reviews] ensure_label({label}) failed: {e}")


async def _add_label(conv_id: int, label: str, show_on_sidebar: bool = True) -> None:
    await _ensure_label_once(label, show_on_sidebar=show_on_sidebar)
    try:
        await chatwoot.add_label(conv_id, label)
    except Exception as e:
        print(f"[web-reviews] add_label({label}) failed for conv {conv_id}: {e}")


async def _ingest_review(rv: dict) -> None:
    """Bring one new website review into Chatwoot as an open conversation."""
    reviewer = _reviewer_name(rv)
    product = f"Product #{rv['product_id']}" if rv.get("product_id") else "Product (unknown)"
    heading = rv["title"] or "(no title)"
    body = (
        f"⭐ {_stars_bar(rv['stars'])}  ({rv['stars'] or '?'}/5)\n"
        f"🛋 {product}\n\n"
        f"{heading}\n"
        f"{rv['comment'] or '(no text — rating only)'}"
    )

    contact_id, source_id = await chatwoot.create_contact(
        name=reviewer,
        identifier=f"webreview_user_{rv.get('user_id') or rv['review_id']}",
        inbox_id=config.WEBSITE_REVIEWS_INBOX_ID,
    )
    conv_id = await chatwoot.create_conversation(
        source_id=source_id or f"wr_{rv['review_id']}",
        inbox_id=config.WEBSITE_REVIEWS_INBOX_ID,
        contact_id=contact_id,
        additional_attributes={"type": "website_review",
                               "review_id": rv["review_id"],
                               "product_id": rv.get("product_id"),
                               "stars": rv["stars"],
                               "reviewer": reviewer,
                               "review_created_at": rv["created"]},
        # Stashed so the outgoing reply handler can post back even if the
        # SQLite conv_map is ever lost/rebuilt.
        custom_attributes={"website_review_id": rv["review_id"]},
    )
    await chatwoot.create_message(conv_id, body, message_type="incoming")

    star_label = f"review-{rv['stars']}star" if rv["stars"] else "review-unrated"
    await _add_label(conv_id, star_label)

    # If durian.in already carries a reply, echo it as an outgoing message and
    # mark replied/resolved so agents see history but don't re-reply. Tagged
    # with AUTO_MARKER so the outgoing webhook doesn't post it back.
    if rv["already_replied"] and rv["reply"]:
        try:
            await chatwoot.create_message(conv_id, rv["reply"], message_type="outgoing",
                                          content_attributes=AUTO_MARKER)
            await _add_label(conv_id, LBL_REPLIED)
        except Exception as e:
            print(f"[web-reviews] echo existing reply failed for conv {conv_id}: {e}")
    else:
        await _add_label(conv_id, LBL_UNREPLIED)

    state.mark_seen(rv["review_id"], conv_id, rv["stars"],
                    replied=rv["already_replied"], update_time=rv["update_time"])
    print(f"[web-reviews] ingested review {rv['review_id']} → conv {conv_id}")


async def poll_once() -> None:
    reviews = await wr.list_reviews()
    cap = config.WEBSITE_REVIEWS_MAX_PER_SWEEP
    new_count = 0
    skipped_for_cap = 0
    for rv in reviews:
        rec = state.seen_record(rv["review_id"])
        if rec is not None:
            # Already ingested. We only re-surface a NEW reply appearing on
            # durian.in (e.g. replied on their side) by flipping our label; the
            # review text itself is immutable enough to skip full edit handling.
            if rv["already_replied"] and not rec.get("replied") and rec.get("conversation_id"):
                await _add_label(rec["conversation_id"], LBL_REPLIED)
                state.mark_replied(rv["review_id"])
            continue
        if cap and new_count >= cap:
            skipped_for_cap += 1
            continue
        try:
            await _ingest_review(rv)
            new_count += 1
        except Exception as e:
            print(f"[web-reviews] ingest failed ({rv['review_id']}): {e}")

    if new_count:
        msg = f"[web-reviews] ingested {new_count} new review(s)"
        if skipped_for_cap:
            msg += f"; {skipped_for_cap} held back by WEBSITE_REVIEWS_MAX_PER_SWEEP={cap} (next sweep)"
        print(msg)


async def run_forever():
    """Background loop. Boot-safe: logs and exits quietly if not configured."""
    if not config.WEBSITE_REVIEWS_ENABLED:
        print("[web-reviews] disabled (WEBSITE_REVIEWS_ENABLED not true) — poller not started")
        return
    if not (config.WEBSITE_REVIEWS_API_TOKEN and config.WEBSITE_REVIEWS_INBOX_ID):
        print("[web-reviews] missing WEBSITE_REVIEWS_API_TOKEN or WEBSITE_REVIEWS_INBOX_ID — poller not started")
        return
    state.init()
    print(f"[web-reviews] poller started · every {config.WEBSITE_REVIEWS_POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            await poll_once()
        except Exception as e:
            print(f"[web-reviews] poll sweep error: {e}")
        await asyncio.sleep(config.WEBSITE_REVIEWS_POLL_INTERVAL_SECONDS)
