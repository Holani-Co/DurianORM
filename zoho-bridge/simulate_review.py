#!/usr/bin/env python3
# Local-only helper: inject a FAKE Google review into Chatwoot so you can test
# the AI suggestion card (edit / regenerate / send / cancel) WITHOUT Google
# Business Profile API access (which is still pending quota approval).
#
# It mirrors what reviews_poller._ingest_review does, but:
#   - never calls Google (no post_reply),
#   - always posts the AI draft as the interactive suggestion card, so you
#     always get a card to play with regardless of star rating.
#
# Run from the zoho-bridge dir with the venv active:
#   python simulate_review.py                       # default 5-star sample
#   python simulate_review.py 2 "Table arrived damaged, no response."  "Priya"
#
# Args (all optional): <stars> <comment> <reviewer>

import asyncio
import sys

import config
import chatwoot
import review_reply


async def main():
    stars = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    comment = sys.argv[2] if len(sys.argv) > 2 else (
        "Absolutely loved the showroom — staff were warm and the sofa quality "
        "is excellent. Will definitely come back!"
    )
    reviewer = sys.argv[3] if len(sys.argv) > 3 else "Test Reviewer"
    location = "Durian Experience Centre - MG Road"
    # Fake but plausible reply path; Send-to-Google will fail locally (expected).
    review_path = "accounts/000/locations/000/reviews/SIMULATED"
    review_id = f"sim_{reviewer}_{stars}".lower().replace(" ", "_")

    if not config.REVIEWS_INBOX_ID:
        raise SystemExit("REVIEWS_INBOX_ID not set in .env")

    stars_bar = "★" * stars + "☆" * (5 - stars)
    body = (f"⭐ {stars_bar}  ({stars}/5)\n📍 {location}\n🗓 (simulated)\n\n{comment}")

    contact_id, source_id = await chatwoot.create_contact(
        name=reviewer,
        identifier=f"greview:{review_id}",
        inbox_id=config.REVIEWS_INBOX_ID,
        custom_attributes={"google_location": location},
    )
    conv_id = await chatwoot.create_conversation(
        source_id=source_id or f"gr_{review_id}",
        inbox_id=config.REVIEWS_INBOX_ID,
        contact_id=contact_id,
        additional_attributes={"type": "google_review", "location": location,
                               "stars": stars, "review_comment": comment,
                               "reviewer": reviewer},
        custom_attributes={"review_path": review_path},
    )
    await chatwoot.create_message(conv_id, body, message_type="incoming")

    reply, action = await review_reply.draft(stars, comment, reviewer, location)
    await chatwoot.create_message(
        conv_id, reply or "(no draft)", message_type="outgoing", private=True,
        content_attributes={"type": "ai_review_suggestion", "suggestion": reply},
    )
    if config.REVIEWS_TEAM_ID:
        try:
            await chatwoot.assign_team(conv_id, config.REVIEWS_TEAM_ID)
        except Exception as e:
            print(f"team assign skipped: {e}")

    print(f"Done. Conversation #{conv_id} created in inbox "
          f"{config.REVIEWS_INBOX_ID} (drafted as '{action}').")
    print(f"Open: {config.CHATWOOT_PUBLIC_URL}/app/accounts/"
          f"{config.CHATWOOT_ACCOUNT_ID}/conversations/{conv_id}")


if __name__ == "__main__":
    asyncio.run(main())
