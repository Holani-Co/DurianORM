# durian.in website product-reviews poller.
#
# On a timer: pull website reviews and, for each new one that has NOT already
# been replied to on durian.in, create a Chatwoot conversation in the Website
# Reviews inbox (contact = reviewer, incoming message = review text + rating)
# and leave it OPEN for a human. An agent's reply is posted back to durian.in by
# main.handle_website_review_reply.
#
# Reviews already answered on the site are skipped (recorded as seen, never
# ingested).
#
# Reply mode is controlled by WEBSITE_REVIEWS_AUTO_REPLY:
#   off (default) → the review is left OPEN for a human (posts back on agent reply)
#   on            → the bridge classifies the review by intent (LLM sentiment,
#                   star fallback for rating-only) and posts the client's approved
#                   positive/negative template back to durian.in, then resolves.

import asyncio
import json

import config
import chatwoot
import website_reviews as wr
import website_reviews_state as state
from llm_client import client

# Marker read by main.handle_website_review_reply: the auto-reply echo below
# carries it so the outgoing webhook does NOT re-post it to durian.in (we already
# posted it directly). Human agent replies don't carry it, so they DO post back.
AUTO_MARKER = {"source": "website_review_echo"}

LBL_REPLIED = "review-replied"
LBL_UNREPLIED = "review-unreplied"

_ensured_labels: set[str] = set()


def _stars_bar(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars) if stars else "(rating unknown)"


def _reviewer_name(rv: dict) -> str:
    if rv.get("reviewer"):
        return rv["reviewer"]
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


# Client's approved response scripts (verbatim). "Dear Person," is replaced with
# the reviewer's first name (or "Dear Customer," when no real name is available).
POSITIVE_TEMPLATE = (
    "Dear Person,\n\n"
    "Thank you so much for your feedback! Your review means a lot to us and "
    "we're really glad you enjoyed the experience. We put customer experience "
    "and satisfaction as our priority, and your review reaffirms the hard work "
    "we put in every day.\n\n"
    "Thanks for your kind words and we look forward to seeing you again.\n\n"
    "Regards,\n"
    "Team Durian"
)
NEGATIVE_TEMPLATE = (
    "Dear Person,\n\n"
    "Thank you for taking the time to share your feedback. We stand behind the "
    "quality of our products — every bed we sell is built with certified "
    "materials, tested craftsmanship, and backed by our warranty. Our pricing "
    "reflects this quality and the after-sales support that comes with it.\n\n"
    "If there is anything specific about the product you'd like us to review, "
    "please write to us at escalation@durian.in with your order details, and "
    "our team will look into it.\n\n"
    "– Team Durian"
)


def _personalise(template: str, reviewer: str) -> str:
    parts = (reviewer or "").strip().split()
    generic = {"website", "reviewer", "customer", "user"}
    first = parts[0].title() if parts and parts[0].lower() not in generic else ""
    salutation = f"Dear {first}," if first else "Dear Customer,"
    return template.replace("Dear Person,", salutation, 1)


async def _classify_sentiment(text: str) -> str:
    """LLM sentiment for a review's text → 'positive' or 'negative'. Any
    complaint/criticism (quality, price, delivery, service) is 'negative'."""
    r = await client.chat.completions.create(
        model=config.OPENAI_MODEL,
        temperature=0,
        max_tokens=10,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": (
                "Classify a furniture customer's product review by overall "
                "sentiment. Respond ONLY as JSON {\"sentiment\": "
                "\"positive\"|\"negative\"}. 'positive' = satisfied or "
                "appreciative. 'negative' = ANY complaint, dissatisfaction or "
                "criticism (quality, pricing, delivery, service, damage).")},
            {"role": "user", "content": text[:1000]},
        ],
    )
    parsed = json.loads(r.choices[0].message.content or "{}")
    return "negative" if str(parsed.get("sentiment", "")).lower().startswith("neg") else "positive"


async def _pick_sentiment(rv: dict) -> str:
    """Intent-first: classify the review text; for a rating-only review (or if
    classification fails) fall back to the star rating."""
    text = (rv.get("comment") or "").strip()
    if text:
        try:
            return await _classify_sentiment(text)
        except Exception as e:
            print(f"[web-reviews] sentiment classify failed ({rv['review_id']}): {e}; using stars")
    return "positive" if (rv.get("stars") or 0) >= config.WEBSITE_REVIEWS_POSITIVE_MIN_STARS else "negative"


async def _auto_reply(rv: dict, conv_id: int) -> str:
    """Post the matching approved template back to durian.in, mirror it into the
    Chatwoot conversation (marked so the webhook doesn't re-post), and resolve.
    Raises if the post-back fails, so the caller leaves the review for a human."""
    sentiment = await _pick_sentiment(rv)
    template = POSITIVE_TEMPLATE if sentiment == "positive" else NEGATIVE_TEMPLATE
    reply = _personalise(template, _reviewer_name(rv))

    await wr.post_reply(rv["review_id"], reply)  # to durian.in first
    await chatwoot.create_message(conv_id, reply, message_type="outgoing",
                                  content_attributes=AUTO_MARKER)
    await _add_label(conv_id, LBL_REPLIED)
    try:
        await chatwoot.toggle_status(conv_id, "resolved")
    except Exception as e:
        print(f"[web-reviews] resolve failed for conv {conv_id}: {e}")
    return sentiment


async def _ingest_review(rv: dict) -> None:
    """Bring one new website review into Chatwoot as an open conversation."""
    reviewer = _reviewer_name(rv)
    product = f"Product #{rv['product_id']}" if rv.get("product_id") else "Product (unknown)"
    meta_bits = [b for b in (rv.get("city"), rv.get("email")) if b]
    heading = rv["title"] or "(no title)"
    body = (
        f"⭐ {_stars_bar(rv['stars'])}  ({rv['stars'] or '?'}/5)\n"
        f"🛋 {product}"
        f"{('  ·  ' + '  ·  '.join(meta_bits)) if meta_bits else ''}\n\n"
        f"{heading}\n"
        f"{rv['comment'] or '(no text — rating only)'}"
    )

    # Key the contact by the UNIQUE review id — each review is its own inbox item.
    # We can't key by email: the site fills a shared placeholder
    # (customersupport@durian.in) for reviews with no customer email, which would
    # collide on Chatwoot's unique email/identifier (422). For the same reason we
    # do NOT set the contact email; the email (if any) is shown in the body.
    contact_id, source_id = await chatwoot.create_contact(
        name=reviewer,
        identifier=f"webreview_review_{rv['review_id']}",
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

    replied = False
    if config.WEBSITE_REVIEWS_AUTO_REPLY:
        try:
            sentiment = await _auto_reply(rv, conv_id)
            replied = True
            print(f"[web-reviews] auto-replied review {rv['review_id']} "
                  f"({sentiment}) → conv {conv_id}")
        except Exception as e:
            # Post-back failed — leave it open for a human rather than losing it.
            print(f"[web-reviews] auto-reply failed for review {rv['review_id']}: {e}")
    if not replied:
        await _add_label(conv_id, LBL_UNREPLIED)

    state.mark_seen(rv["review_id"], conv_id, rv["stars"],
                    replied=replied, update_time=rv["update_time"])
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
        # Already answered on durian.in → record as seen (so we don't reconsider
        # it) but DON'T bring it into the inbox. We only surface reviews that
        # still need a human reply, and we never auto-reply.
        if rv["already_replied"]:
            state.mark_seen(rv["review_id"], 0, rv["stars"],
                            replied=True, update_time=rv["update_time"])
            continue
        # Backlog cutoff: the list has ~23k historical reviews and no `since`
        # filter, so without this the inbox floods with years of old reviews.
        # Skip (record as seen) anything created before WEBSITE_REVIEWS_SINCE.
        since = config.WEBSITE_REVIEWS_SINCE
        if since and rv["created"] and rv["created"] < since:
            state.mark_seen(rv["review_id"], 0, rv["stars"],
                            replied=False, update_time=rv["update_time"])
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
