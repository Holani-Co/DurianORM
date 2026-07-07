# Google Reviews poller.
#
# On a timer: pull recent reviews for every GBP location, and for each NEW one:
#   1. create a Chatwoot conversation in the Google Reviews inbox
#      (contact = reviewer, incoming message = the review text + rating + city)
#   2. draft an AI reply in Durian voice (rating-aware)
#   3. if "auto"  → post the reply to Google AND record it in Chatwoot
#                   (marked so the webhook doesn't re-post), then resolve.
#      if "handoff" → leave the AI suggestion as a PRIVATE note and leave it
#                     open in the reviews inbox for an agent (no team assign).
#
# Locations are discovered once and cached for the process lifetime.

import asyncio
import re
from datetime import datetime, timedelta, timezone

import config
import chatwoot
import google_reviews as gr
import review_reply
import reviews_state as state
import tracing

# India Standard Time — Durian's locations are all in India, so reviews always
# render in the showroom's local time, not UTC.
_IST = timezone(timedelta(hours=5, minutes=30))

# content_attributes marker so the Chatwoot webhook can tell an auto-reply
# (already posted to Google) apart from a human reply (needs posting).
AUTO_MARKER = {"source": "google_auto_reply"}

_locations_cache: list[dict] = []


def _stars_bar(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars) if stars else "(rating unknown)"


def _store_label(title: str) -> str:
    """Filterable label for the showroom a review came from, e.g.
    'Durian - Koramangala' → 'store-durian-koramangala'. Slugified so it's a
    clean, stable Chatwoot label agents can filter the conversation list by."""
    slug = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return f"store-{slug}" if slug else "store-unknown"


def agent_name_slug(name: str, email: str = "", agent_id=None) -> str:
    """Slug used in the `replied-by-<slug>` label. Falls back through name →
    email-local-part → id so we NEVER produce an empty slug (the label would
    otherwise be `replied-by-` and match no conversations). Kept alongside
    _store_label so all bridge-managed slug labels share one rule."""
    raw = (name or "").strip()
    if not raw and email:
        raw = email.split("@", 1)[0]
    slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    if slug:
        return slug
    return str(agent_id) if agent_id else ""


# Rating labels get a traffic-light colour so the Settings → Labels list and
# the sidebar chips read at a glance; stores share a neutral blue.
_LABEL_COLORS = {
    "review-1star": "#d7263d", "review-2star": "#f46036",
    "review-3star": "#f5a623", "review-4star": "#7cb342",
    "review-5star": "#2e7d32", "review-unrated": "#8a8a8a",
}

# Labels added to a conversation are taggings; they only become filterable
# (dropdowns / Settings → Labels) once a Label record exists. Ensure that once
# per process per label.
_ensured_labels: set = set()


async def _ensure_label_once(title: str, show_on_sidebar: bool = True) -> None:
    if title in _ensured_labels:
        return
    try:
        await chatwoot.ensure_label(title, _LABEL_COLORS.get(title, "#1f93ff"),
                                    show_on_sidebar=show_on_sidebar)
    except Exception as e:
        print(f"[reviews] ensure_label({title}) failed: {e}")
    _ensured_labels.add(title)


# Reply-status / reply-type labels backing the reviews inbox filter dropdown.
# Hidden from the sidebar (they're dropdown filters, not sidebar chips).
#   review-unreplied         — handed off, no reply posted yet
#   review-replied           — a reply has been posted (auto OR manual)
#   review-auto-replied      — the system auto-replied (positive high-star)
#   review-manually-replied  — an agent approved a template and replied
LBL_UNREPLIED        = "review-unreplied"
LBL_REPLIED          = "review-replied"
LBL_AUTO_REPLIED     = "review-auto-replied"
LBL_MANUALLY_REPLIED = "review-manually-replied"


async def tag_reply_status(conv_id: int, *add: str, remove: tuple = ()) -> None:
    """Add/remove reply-status labels on a review conversation (best-effort)."""
    for lbl in add:
        await _ensure_label_once(lbl, show_on_sidebar=False)
        try:
            await chatwoot.add_label(conv_id, lbl)
        except Exception as e:
            print(f"[reviews] add_label({lbl}) failed for conv {conv_id}: {e}")
    for lbl in remove:
        try:
            await chatwoot.remove_label(conv_id, lbl)
        except Exception as e:
            print(f"[reviews] remove_label({lbl}) failed for conv {conv_id}: {e}")


def _format_review_time(iso: str) -> str:
    """Google returns the review timestamp as ISO 8601 UTC (e.g.
    `2022-06-08T12:47:48.747377Z`) — readable to a machine but ugly in the
    Chatwoot card. Render as IST in a friendly form, e.g. "8 Jun 2022, 6:17 PM".
    Falls back to the raw value if parsing fails (don't break the card)."""
    if not iso:
        return ""
    try:
        # Python's fromisoformat handles "...+00:00" but not the "Z" suffix
        # until 3.11 — normalise manually for portability.
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(_IST)
        # %d / %I pad with leading zeros on every platform; strip both manually
        # for a cleaner "8 Jun 2022, 6:17 PM IST" instead of "08 ... 06:17 PM".
        return (f"{dt.day} {dt.strftime('%b %Y')}, "
                f"{dt.hour % 12 or 12}:{dt.strftime('%M %p')} IST")
    except Exception:
        return iso


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
        f"🗓 {_format_review_time(rv['create_time'])}\n\n"
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
        # review_created_at = the review's ACTUAL date on Google (not the
        # ingestion time). Stored so the reviews inbox can sort/display by real
        # review date instead of when we happened to pull it in.
        additional_attributes={"type": "google_review", "location": title,
                               "stars": rv["stars"],
                               "review_comment": rv["comment"] or "",
                               "reviewer": rv["reviewer"] or "",
                               "review_created_at": rv["create_time"] or ""},
        custom_attributes={"review_path": rv["reply_path"]},
    )
    review_msg = await chatwoot.create_message(conv_id, body, message_type="incoming")
    review_msg_id = review_msg.get("id")

    # Star-rating label so agents can filter by ★ in Chatwoot's sidebar
    # (Labels → review-5star, review-1star, …). Labels auto-create on first
    # use server-side. Best-effort: a failure here doesn't block ingestion.
    # Star + store labels so agents can filter reviews by rating and showroom
    # (the Google Reviews inbox dropdowns / Settings → Labels). ensure_label
    # creates the Label record so it's selectable; add_label merges, so the
    # two labels coexist on the conversation.
    star_label = f"review-{rv['stars']}star" if rv['stars'] else "review-unrated"
    store_label = _store_label(title)
    # Store labels are hidden from the sidebar (100+ of them) — they exist only
    # to back the inbox store dropdown. Star labels stay sidebar-visible (~7).
    for lbl, on_sidebar in ((star_label, True), (store_label, False)):
        await _ensure_label_once(lbl, show_on_sidebar=on_sidebar)
        try:
            await chatwoot.add_label(conv_id, lbl)
        except Exception as e:
            print(f"[reviews] add_label({lbl}) failed for conv {conv_id}: {e}")

    # 2. AI draft — ALWAYS produce a card so the agent has a template ready,
    # even when Google already has a reply on this review. The has_reply flag
    # below only gates auto-posting (we won't re-post to Google), not the card.
    _lf = tracing.message_parent(conv_id, review_msg_id, name="review-reply",
                                 stars=rv["stars"], location=title)
    drafted = await review_reply.draft(
        channel="review",
        message=rv["comment"] or "",
        contact_name=rv["reviewer"] or "Customer",
        stars=rv["stars"] or 0,
        location=title,
        lf_parent=_lf,
    )
    reply, action = drafted["reply"], drafted["action"]

    # Flag genuinely-bad reviews for the "Escalate to team" button: the AI
    # hands off (action == "handoff") for any complaint/criticism or low
    # rating, so review_negative marks reviews the team may want to escalate.
    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"review_negative": action == "handoff"})
    except Exception as e:
        print(f"[reviews] review_negative flag failed for conv {conv_id}: {e}")

    if action == "auto" and config.REVIEWS_AUTO_REPLY and reply and not rv["has_reply"]:
        # 3a. Post to Google, then mirror into Chatwoot (marked) + resolve.
        try:
            await gr.post_reply(rv["reply_path"], reply)
            await chatwoot.create_message(conv_id, reply, message_type="outgoing",
                                          content_attributes=AUTO_MARKER)
            await chatwoot.toggle_status(conv_id, "resolved")
            await tag_reply_status(conv_id, LBL_REPLIED, LBL_AUTO_REPLIED)
            state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"],
                            replied=True, update_time=rv["update_time"])
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
    # No team assignment for reviews — teams exist for email routing and a team
    # box on every review is just noise. The conversation stays in the reviews
    # inbox (unassigned, open) for an agent to pick up directly.
    await tag_reply_status(conv_id, LBL_UNREPLIED)
    state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"],
                    replied=False, update_time=rv["update_time"])
    print(f"[reviews] handoff {rv['stars']}★ @ {title} → human")


LBL_EDITED = "review-edited"


async def _ingest_edit(loc: dict, rv: dict, rec: dict):
    """A previously-seen review was EDITED on Google (same reviewId, newer
    updateTime). Re-surface it in the EXISTING conversation — new text as an
    incoming message, a fresh AI suggestion card, reopened + back to unreplied
    — so the team is prompted to send an updated reply (the client's case: a
    customer turns a complaint into praise and now wants a warm response).
    Never auto-posts to Google — an edit always goes to a human."""
    conv_id = rec.get("conversation_id")
    if not conv_id:
        # Seeded/link-less record — no conversation to update; ingest fresh.
        await _ingest_review(loc, rv)
        return

    title = loc["title"]
    old_stars = rec.get("stars") or 0
    star_changed = (rv["stars"] or 0) != old_stars
    header = "✏️ *Review edited on Google*"
    if star_changed:
        header += f" — rating changed {old_stars or '?'}★ → {rv['stars'] or '?'}★"
    # Show BOTH dates: when it was originally posted (create_time — unchanged
    # by an edit) and when it was edited (update_time — the new timestamp).
    date_line = f"🗓 Posted {_format_review_time(rv['create_time'])}"
    if rv["update_time"]:
        date_line += f"  ·  ✏️ Edited {_format_review_time(rv['update_time'])}"
    body = (
        f"{header}\n\n"
        f"⭐ {_stars_bar(rv['stars'])}  ({rv['stars'] or '?'}/5)\n"
        f"📍 {title}\n"
        f"{date_line}\n\n"
        f"{rv['comment'] or '(no text — rating only)'}"
    )
    review_msg = await chatwoot.create_message(conv_id, body, message_type="incoming")
    review_msg_id = review_msg.get("id")

    # Stash the edited text/rating so the "Regenerate" button re-drafts from
    # the NEW review, not the original (the original lives in
    # additional_attributes, which the API can't update — the review regenerate
    # handler prefers these custom_attributes when present).
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "review_edited_comment": rv["comment"] or "",
            "review_edited_stars": rv["stars"] or 0,
        })
    except Exception as e:
        print(f"[reviews] edit: merge_custom_attributes failed conv {conv_id}: {e}")

    # Swap the star label if the rating changed (e.g. review-2star → review-4star).
    if star_changed:
        old_lbl = f"review-{old_stars}star" if old_stars else "review-unrated"
        new_lbl = f"review-{rv['stars']}star" if rv['stars'] else "review-unrated"
        await _ensure_label_once(new_lbl, show_on_sidebar=True)
        try:
            await chatwoot.remove_label(conv_id, old_lbl)
            await chatwoot.add_label(conv_id, new_lbl)
        except Exception as e:
            print(f"[reviews] edit: star relabel failed conv {conv_id}: {e}")

    # `review-edited` marker so the team can spot/filter re-surfaced reviews.
    await _ensure_label_once(LBL_EDITED, show_on_sidebar=True)
    try:
        await chatwoot.add_label(conv_id, LBL_EDITED)
    except Exception as e:
        print(f"[reviews] edit: add {LBL_EDITED} failed conv {conv_id}: {e}")

    # Fresh AI draft from the NEW text, posted as the suggestion card.
    _lf = tracing.message_parent(conv_id, review_msg_id, name="review-edit",
                                 stars=rv["stars"], location=title)
    drafted = await review_reply.draft(
        channel="review", message=rv["comment"] or "",
        contact_name=rv["reviewer"] or "Customer",
        stars=rv["stars"] or 0, location=title, lf_parent=_lf,
    )
    reply = drafted["reply"]
    # Re-evaluate the escalate flag — an edit can flip a complaint to praise.
    try:
        await chatwoot.merge_custom_attributes(
            conv_id, {"review_negative": drafted["action"] == "handoff"})
    except Exception as e:
        print(f"[reviews] edit: review_negative flag failed for conv {conv_id}: {e}")
    note = reply or "(AI flagged this edited review for human handling — no draft.)"
    await chatwoot.create_message(
        conv_id, note, message_type="outgoing", private=True,
        content_attributes={"type": "ai_review_suggestion",
                            "suggestion": reply, "channel": "review",
                            "ai_trace": drafted["trace"]},
    )

    # Reopen + reset to unreplied so it re-enters the team's queue for a new reply.
    try:
        await chatwoot.toggle_status(conv_id, "open")
    except Exception as e:
        print(f"[reviews] edit: reopen failed conv {conv_id}: {e}")
    await tag_reply_status(conv_id, LBL_UNREPLIED,
                           remove=(LBL_REPLIED, LBL_AUTO_REPLIED, LBL_MANUALLY_REPLIED))
    state.mark_seen(rv["review_id"], conv_id, rv["reply_path"], rv["stars"],
                    replied=False, update_time=rv["update_time"])
    print(f"[reviews] EDIT re-surfaced {rv['stars']}★ @ {title} (conv {conv_id})")


async def poll_once():
    """One sweep across all locations. Bounded by REVIEWS_MAX_PER_SWEEP so the
    first run after enabling the poller doesn't flood the inbox with the
    location's entire historical backlog — the rest gets picked up on
    subsequent sweeps, paced by REVIEWS_POLL_INTERVAL_SECONDS."""
    locations = await _discover_locations()
    cap = config.REVIEWS_MAX_PER_SWEEP
    new_count = 0
    edit_count = 0
    skipped_for_cap = 0
    for loc in locations:
        try:
            reviews = await gr.list_reviews(loc["account_id"], loc["location_id"])
        except Exception as e:
            print(f"[reviews] list failed for {loc['title']}: {e}")
            continue
        for rv in reviews:
            rec = state.seen_record(rv["review_id"])
            if rec is not None:
                # Already seen — but was it EDITED? Compare Google's updateTime
                # to what we stored.
                stored = rec.get("update_time") or ""
                if not stored:
                    # Baseline unknown (seeded, or ingested before we tracked
                    # updateTime) → record the current one WITHOUT re-surfacing,
                    # so we don't fire a spurious edit for every old review.
                    state.mark_seen(rv["review_id"], rec.get("conversation_id") or 0,
                                    rv["reply_path"], rec.get("stars") or 0,
                                    replied=bool(rec.get("replied")),
                                    update_time=rv["update_time"])
                    continue
                if rv["update_time"] and rv["update_time"] != stored:
                    try:
                        await _ingest_edit(loc, rv, rec)
                        edit_count += 1
                    except Exception as e:
                        print(f"[reviews] edit re-surface failed ({rv['review_id']}): {e}")
                continue
            if cap and new_count >= cap:
                skipped_for_cap += 1
                continue
            try:
                await _ingest_review(loc, rv)
                new_count += 1
            except Exception as e:
                print(f"[reviews] ingest failed ({rv['review_id']}): {e}")
    if new_count or edit_count:
        msg = f"[reviews] ingested {new_count} new review(s), {edit_count} edited"
        if skipped_for_cap:
            msg += (f"; {skipped_for_cap} held back by "
                    f"REVIEWS_MAX_PER_SWEEP={cap} (next sweep)")
        print(msg)


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
