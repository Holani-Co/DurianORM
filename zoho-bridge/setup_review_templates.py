#!/usr/bin/env python3
# One-time helper: seed Durian's Google-review reply templates into Chatwoot
# as Canned Responses. After this runs, the team can edit/add/remove templates
# straight from the Chatwoot UI (Settings → Canned Responses) — no code change.
#
# The AI suggester (review_reply.py) reads these same canned responses at reply
# time, so editing a template in the UI immediately changes what the AI drafts.
#
# Run from the zoho-bridge dir with the venv active:
#   python setup_review_templates.py
#
# Safe to re-run: existing short_codes are skipped (never overwritten — we don't
# want to clobber edits the team made in the UI).

import asyncio
import httpx
import config

# short_code → template body. short_codes are the stable key the AI matches on;
# keep them descriptive. Bodies are the current Durian-approved wording.
TEMPLATES = {
    "review_positive_5star": (
        "Dear Customer,\n\n"
        "Thank you so much for your feedback! Your review means a lot to us "
        "and we're really glad you enjoyed the experience.\n\n"
        "We put customer experience and satisfaction as our priority, and your "
        "review reaffirms the hard work we put in every day.\n\n"
        "Thanks for your kind words and we look forward to seeing you again.\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_positive_can_improve": (
        "Dear Customer,\n\n"
        "Thanks so much for your feedback! We're really glad you enjoyed the "
        "experience and appreciate you taking the time to share your feedback "
        "with us.\n\n"
        "We've taken your feedback and will definitely work to include your "
        "recommendations.\n\n"
        "Thank you again for taking the time to share your experience and we "
        "look forward to seeing you again.\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_negative_info_needed": (
        "Dear Customer,\n\n"
        "To help us route your complaint to the appropriate team, we request "
        "you to re-share your contact details.\n\n"
        "Our team will get in touch with you at the earliest. For quick "
        "guidance, you can connect with us on: customersupport@durian.in, "
        "WhatsApp: 8591108987 or call on Customer support: 1800 209 3242\n\n"
        "Stay safe,\nRegards,\nTeam Durian."
    ),
    "review_negative_will_work_on_it": (
        "Dear Customer,\n\n"
        "We are sorry your experience didn't match your expectations. It was "
        "an uncommon instance and we will do better in the future.\n\n"
        "Our team will get in touch with you and work with you to resolve this "
        "as quickly as possible.\n\n"
        "Thank you for your patience and we assure you that we will make things "
        "right and deliver the excellent experience we are known for.\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_issue_resolved": (
        "Dear Customer,\n\n"
        "We hope that we were able to resolve your concern satisfactorily and "
        "deliver the excellent experience we are known for.\n\n"
        "We take pride in being able to cater to our customer's needs as much "
        "as possible, so we are truly sorry for the inconvenience caused and "
        "assure you to provide a better experience in the future.\n\n"
        "Thank you for your patience and we hope to see you again.\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_issue_not_resolved": (
        "Dear Customer,\n\n"
        "We're very sorry you had this experience. Please know that your "
        "situation was an exception and we understand how frustrating this "
        "must have been for you. We take customer experience very seriously "
        "and our team will get in touch with you and work with you to resolve "
        "this as quickly as possible.\n\n"
        "We are truly sorry for the inconvenience and we assure you that we "
        "will make things right. Thank you for your patience!\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_acknowledge_feedback": (
        "Dear Customer,\n\n"
        "Thanks for taking the time to share your valuable feedback with us.\n\n"
        "I have passed your thoughts on to the team and we will definitely work "
        "to include your recommendations.\n\n"
        "Thank you again for taking the time to share your experience and we "
        "look forward to seeing you again.\n\n"
        "Regards,\nTeam Durian"
    ),
    "review_resolved_negative": (
        "Dear Customer,\n\n"
        "We apologize for the less than perfect experience. At Durian, our "
        "customers are our top priority and we diligently work towards "
        "resolving every issue. We have presented you with all possible "
        "resolutions and would request you to reconsider the same. We will "
        "strive to offer you better services in the future to earn your "
        "continued trust and support. We truly regret the inconvenience "
        "caused.\n\n"
        "Stay safe,\nRegards,\nTeam Durian."
    ),
}


def _headers():
    return {"api_access_token": config.CHATWOOT_API_TOKEN, "Content-Type": "application/json"}


def _url(suffix):
    return f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}{suffix}"


async def main():
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(_url("/canned_responses"), headers=_headers())
        r.raise_for_status()
        existing = {cr["short_code"] for cr in r.json()}

        created, skipped = 0, 0
        for short_code, content in TEMPLATES.items():
            if short_code in existing:
                print(f"  skip (exists): {short_code}")
                skipped += 1
                continue
            resp = await client.post(
                _url("/canned_responses"),
                headers=_headers(),
                json={"short_code": short_code, "content": content},
            )
            if resp.status_code >= 300:
                print(f"  FAILED {short_code} [{resp.status_code}]: {resp.text}")
                continue
            print(f"  created: {short_code}")
            created += 1

        print(f"\nDone. {created} created, {skipped} already existed.")


if __name__ == "__main__":
    asyncio.run(main())
