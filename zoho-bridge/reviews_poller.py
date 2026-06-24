# Google Reviews poller.
#
# On a timer: pull recent reviews for every GBP location, and for each NEW one:
#   1. create a Chatwoot conversation in the Google Reviews inbox
#      (contact = reviewer, incoming message = the review text + rating + city)
#   2. draft an AI reply in Durian voice (rating-aware)
#   3. if "auto"  → post the reply to Google AND record it in Chatwoot
#                   (marked so the webhook doesn't re-post), then resolve.
#      if "handoff" → leave the AI suggestion as a PRIVATE note, assign the
#                     reviews team, and leave it open for a human.
#
# Locations are discovered once and cached for the process lifetime.

import asyncio

import config
import chatwoot
import google_reviews as gr
import review_reply
import reviews_state as state

# content_attributes marker so the Chatwoot webhook can tell an auto-reply
# (already posted to Google) apart from a human reply (needs posting).
AUTO_MARKER = {"source": "google_auto_reply"}

_locations_cache: list[dict] = []


def _stars_bar(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars) if stars else "(rating unknown)"


async def _discover_locations() -> list[dict]:
    """List every (account_id, location_id, title). Cached after first call."""
    global _locations_cache
    if _locations_cache:
        return _locations_cache
    found = []
    account_ids = [config.GBP_ACCOUNT_ID] if config.GBP_ACCOUNT_ID else await gr.list_account_ids()
    for acct in account_ids:
        acct_num = acct.split("/")[-1]
        for loc in await gr.list_locations(acct if acct.startswith("accounts/") else f"accounts/{acct_num}"):
            found.append({"account_id": acct_num, "location_id": loc["id"], "title": loc["title"]})
    _locations_cache = found
    print(f"[reviews] discovered {len(found)} locations")
    return found


async def _ingest_review(loc: dict, rv: dict):
    """Bring one new review into Chatwoot + reply or hand off."""
    title = loc["title"]
    body = (
        f"⭐ {_stars_bar(rv['stars'])}  ({rv['stars'] or '?'}/5)\n"
        f"📍 {title}\n"
        f"🗓 {rv['create_time']}\n\n"
        f"{rv['comment'] or '(no text — rating only)'}"
    )

    # 1. Contact + conversation in the reviews inbox
    contact_id, source_id = await chatwoot.create_contact(
        name=rv["reviewer"],
        identifier=f"greview:{rv['reviewer']}".lower().replace(" ", "_"),
        inbox_id=config.REVIEWS_INBOX_ID,
        custom_attributes={"google_location": title},
    )
    conv_id = await chatwoot.create_conversation(
        source_id=source_id or f"gr_{rv['review_id']}",
        inbox_id=config.REVIEWS_INBOX_ID,
        contact_id=contact_id,
        # review_comment + reviewer are stashed here so the "Regenerate"
        # button can re-draft from the original review without re-parsing the
        # formatted message body.
        additional_attributes={"type": "google_review", "location": title,
                               "stars": rv["stars"],
                               "review_comment": rv["comment"] or "",
                               "reviewer": rv["reviewer"] or ""},
        custom_attributes={"review_path": rv["reply_path"]},
    )
    await chatwoot.create_message(conv_id, body, message_type="incoming")

    # If the review already has a reply on Google, just record it — no action.
    if rv["has_reply"]:
        state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"], replied=True)
        return

    # 2. AI draft
    drafted = await review_reply.draft(
        channel="review",
        message=rv["comment"] or "",
        contact_name=rv["reviewer"] or "Customer",
        stars=rv["stars"] or 0,
        location=title,
    )
    reply, action = drafted["reply"], drafted["action"]

    if action == "auto" and config.REVIEWS_AUTO_REPLY and reply:
        # 3a. Post to Google, then mirror into Chatwoot (marked) + resolve.
        try:
            await gr.post_reply(rv["reply_path"], reply)
            await chatwoot.create_message(conv_id, reply, message_type="outgoing",
                                          content_attributes=AUTO_MARKER)
            await chatwoot.toggle_status(conv_id, "resolved")
            state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"], replied=True)
            print(f"[reviews] auto-replied {rv['stars']}★ @ {title}")
            return
        except Exception as e:
            print(f"[reviews] auto-reply failed, handing off: {e}")

    # 3b. Handoff: post the AI draft as an interactive suggestion card. The
    # content_attributes marker makes the Chatwoot frontend render it as the
    # "AI suggested reply" card (edit / regenerate / send / cancel). `content`
    # holds the plain text too, so it still reads fine if the card doesn't
    # render (e.g. older frontend).
    note = reply or "(AI flagged this review for human handling — no draft.)"
    await chatwoot.create_message(
        conv_id, note, message_type="outgoing", private=True,
        content_attributes={"type": "ai_review_suggestion",
                            "suggestion": reply, "channel": "review",
                            "ai_trace": drafted["trace"]},
    )
    if config.REVIEWS_TEAM_ID:
        try:
            await chatwoot.assign_team(conv_id, config.REVIEWS_TEAM_ID)
        except Exception as e:
            print(f"[reviews] team assign failed: {e}")
    state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"], replied=False)
    print(f"[reviews] handoff {rv['stars']}★ @ {title} → human")


async def poll_once():
    """One sweep across all locations."""
    locations = await _discover_locations()
    new_count = 0
    for loc in locations:
        try:
            reviews = await gr.list_reviews(loc["account_id"], loc["location_id"])
        except Exception as e:
            print(f"[reviews] list failed for {loc['title']}: {e}")
            continue
        for rv in reviews:
            if state.is_seen(rv["review_id"]):
                continue
            try:
                await _ingest_review(loc, rv)
                new_count += 1
            except Exception as e:
                print(f"[reviews] ingest failed ({rv['review_id']}): {e}")
    if new_count:
        print(f"[reviews] ingested {new_count} new review(s)")


async def run_forever():
    """Background loop. Boot-safe: logs and exits quietly if not configured."""
    if not config.GOOGLE_REVIEWS_ENABLED:
        print("[reviews] disabled (GOOGLE_REVIEWS_ENABLED not true) — poller not started")
        return
    if not (config.GOOGLE_CLIENT_ID and config.GOOGLE_REFRESH_TOKEN and config.REVIEWS_INBOX_ID):
        print("[reviews] missing Google creds or REVIEWS_INBOX_ID — poller not started")
        return
    state.init()
    print(f"[reviews] poller started · every {config.REVIEWS_POLL_INTERVAL_SECONDS}s")
    while True:
        try:
            await poll_once()
        except Exception as e:
            print(f"[reviews] poll sweep error: {e}")
        await asyncio.sleep(config.REVIEWS_POLL_INTERVAL_SECONDS)
