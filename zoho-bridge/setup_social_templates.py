#!/usr/bin/env python3
# Seed Durian's social-media (Instagram / Facebook DM + comment) reply
# templates into Chatwoot as Canned Responses, transcribed from Durian's
# "Social Media - Response Template - 2025" sheet and cleaned for sending:
#   - internal notes removed ("(TAG THE PERSON IF POSSIBLE)", "[Response in
#     DM wrt...]")
#   - the "Delhi" → "DelHello" find/replace bug in the source fixed
#   - placeholders normalised to [NAME] / [PRODUCT_NAME] / [PRODUCT_URL]
#   - whitespace + sign-off standardised
# The wording is otherwise faithful to Durian's approved responses.
#
# Channel prefix is `social_` (Instagram + Facebook share one set — Durian's
# sheet labels nearly every template "FB & IG DM"). Vertical is baked into the
# short_code where the wording differs (furniture / door / wardrobe / fhc), so
# the AI/agent picks the right one and the CONTENT stays pure sendable text.
# WhatsApp is intentionally absent — Durian has no WhatsApp templates yet.
#
# Run from the zoho-bridge dir with the venv active:
#   python setup_social_templates.py
# Safe to re-run: existing short_codes are skipped (never overwrites UI edits).

import asyncio
import httpx
import config

SUPPORT = "📞 Customer support: 1800 209 3242"

TEMPLATES = {
    # ── Price / product enquiry (per vertical) ─────────────────────────────
    "social_price_furniture": (
        "Hello [NAME],\n\n"
        "Thank you for reaching out. We are more than happy to help you.\n\n"
        "🔗 Here's the link for the product details & price of [PRODUCT_NAME] "
        "(e.g. Berry Leatherette Sofa): [PRODUCT_URL]\n\n"
        f"For quick guidance, connect with us on:\n{SUPPORT}\n\n"
        "You can also share your contact details for our customer executive to "
        "get in touch with you. We'll be happy to help you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_price_door": (
        "Hello [NAME],\n\n"
        "Thank you for your interest in Durian doors.\n\n"
        "The price of doors may vary depending upon your requirement. Hence, we "
        "request you to kindly share your contact details for our executive to "
        "get in touch with you for further assistance.\n\n"
        f"For quick guidance, connect with us on:\n{SUPPORT}\n\n"
        "For more information on Durian doors, check out our catalog: "
        "https://www.durian.in/catalog/retail-door-catalog-2020\n\n"
        "We'll be happy to help you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_price_wardrobe": (
        "Hello [NAME],\n\n"
        "Thank you for your interest in Durian wardrobes.\n\n"
        "We have a wide range of modular wardrobes. The price may vary depending "
        "upon your requirement. Hence, we request you to kindly share your "
        "contact details for our executive to get in touch with you for further "
        "assistance.\n\n"
        f"For quick guidance, connect with us on:\n{SUPPORT}\n\n"
        "For more information on Durian wardrobes, please visit: "
        "https://www.durian.in/wardrobe\n\n"
        "We'll be happy to help you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_price_fhc": (
        "Hello [NAME],\n\n"
        "Thank you for your interest in Full Home Customisation.\n\n"
        "As our solutions are tailored to your unique needs, we'd like to give "
        "you the most accurate quote. Book your appointment today and connect "
        "with our Design Experts to discuss your design requirements in "
        "detail.\n\n"
        "Contact us at 1800 209 3242 to book your appointment.\n\n"
        "Regards,\nTeam Durian."
    ),

    # ── Catalogue (per vertical) ───────────────────────────────────────────
    "social_catalogue_door": (
        "Hello [NAME],\n\n"
        "Please find the link to the Durian Doors catalogue: "
        "http://duriandoors.in/catalog/durian-doors-2020\n\n"
        "We look forward to hearing from you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_catalogue_wardrobe": (
        "Hello [NAME],\n\n"
        "Please find the link to the Durian Modular Customised Wardrobes "
        "catalogue: https://www.durian.in/catalog/durian-wardrobe-collection\n\n"
        "We look forward to hearing from you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_catalogue_furniture": (
        "Hello [NAME],\n\n"
        "Please find the link to the Durian Bedroom & Furniture collection "
        "catalogue: "
        "https://www.durian.in/catalog/durian-furniture-collection-2020\n\n"
        "We look forward to hearing from you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_catalogue_fhc": (
        "Hello [NAME],\n\n"
        "Thank you for your interest. Please find the link to the catalogue for "
        "Durian Full Home Customisation: [PRODUCT_URL]\n\n"
        "Contact us at 1800 209 3242 to book your appointment. We look forward "
        "to hearing from you.\n\n"
        "Regards,\nTeam Durian."
    ),

    # ── Address / store enquiry ────────────────────────────────────────────
    "social_address": (
        "Hello [NAME],\n\n"
        "Thank you for your interest.\n\n"
        "You can locate the Durian store near you by referring to this store "
        "locator link: https://www.durian.in/stores\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_address_door": (
        "Hello [NAME],\n\n"
        "Thank you for your interest. Currently, our doors division is only "
        "available in Indore, Bangalore, Delhi, Mumbai & Hyderabad.\n\n"
        "We request you to share your contact details so that our executives "
        "can get in touch with you and assist you in the best way possible.\n\n"
        "For more product details, kindly visit: "
        "https://www.durian.in/catalog/retail-door-catalog-2020\n\n"
        "We'll be happy to help you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_address_fhc": (
        "Hello [NAME],\n\n"
        "Thank you for your interest in Full Home Customisation.\n\n"
        "You can easily find your nearest Durian Full Home Customisation Studio "
        "by visiting: https://www.durian.in/stores\n\n"
        "We look forward to assisting you in creating your dream space! For more "
        "information, feel free to reach out to us at 1800 209 3242.\n\n"
        "Warm regards,\nTeam Durian."
    ),

    # ── Contact shared / contact details ───────────────────────────────────
    "social_contact_shared_ack": (
        "Hello [NAME],\n\n"
        "Thank you for your interest and for sharing your contact details.\n\n"
        "Our executives will get in touch with you shortly.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_contact_details": (
        "Hello [NAME],\n\n"
        "Thank you for your interest. Please find the contact details below:\n\n"
        f"{SUPPORT}\n\n"
        "Regards,\nTeam Durian."
    ),

    # ── Appreciation ───────────────────────────────────────────────────────
    "social_appreciation_5star": (
        "Dear Customer,\n\n"
        "Thank you so much for your feedback! Your review means a lot to us and "
        "we're really glad you enjoyed the experience.\n\n"
        "We put customer experience and satisfaction as our priority, and your "
        "review reaffirms the hard work we put in every day.\n\n"
        "Thanks for your kind words and we look forward to seeing you again.\n\n"
        "Regards,\nTeam Durian"
    ),

    # ── Complaints ─────────────────────────────────────────────────────────
    "social_complaint_info_needed": (
        "Dear Customer,\n\n"
        "We are sorry for the experience you had. To ensure your concern is "
        "routed to the appropriate team, we kindly request you to re-share your "
        "contact details.\n\n"
        "Our team will get in touch with you at the earliest. For immediate "
        "assistance, feel free to connect with us through:\n"
        "📧 Email: customersupport@durian.in\n"
        "📱 WhatsApp: 8591108987\n"
        "📞 Customer Support: 1800 209 3242\n\n"
        "We appreciate your patience and look forward to resolving your "
        "issue.\n\n"
        "Warm regards,\nTeam Durian"
    ),
    "social_complaint_phone_shared": (
        "Hello [NAME],\n\n"
        "We sincerely regret the inconvenience caused and will look into the "
        "matter at the earliest.\n\n"
        "We have successfully registered your query. Our executives will get in "
        "touch with you shortly.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_fraud_concern": (
        "Hello [NAME],\n\n"
        "Thank you for sharing your concerns with us. We greatly value all "
        "feedback, whether positive or constructive, as it helps us continuously "
        "improve your experience with us.\n\n"
        "Your input is highly appreciated, and we're here to assist you further. "
        "Please don't hesitate to reach out for any additional support:\n"
        "📱 WhatsApp: 8591108987\n"
        "📞 Customer Support: 1800 209 3242\n\n"
        "Thank you for your understanding.\n\n"
        "Warm regards,\nTeam Durian"
    ),

    # ── Other enquiries ────────────────────────────────────────────────────
    "social_product_exchange": (
        "Hello [NAME],\n\n"
        "Thank you for your interest.\n\n"
        "Kindly visit your nearest Durian store to know more about available "
        "exchange offers. To find your nearest store, refer to this store "
        "locator link: https://www.durian.in/stores\n\n"
        f"For quick guidance, connect with us on:\n{SUPPORT}\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_ready_stock": (
        "Hello [NAME],\n\n"
        "Thanks for reaching out!\n\n"
        "If you're looking for beds, bedside tables, or bedroom storage, explore "
        "our range here: https://www.durian.in/buy-furniture/bedroom-furniture\n\n"
        "For modular kitchens or wardrobes, you can explore our custom solutions "
        "here: https://www.durian.in/full-home-customisation/products-offerings\n\n"
        "If you have any other queries, please let us know and one of our "
        "executives will be happy to assist you.\n\n"
        "Regards,\nTeam Durian"
    ),
    "social_fhc_intro": (
        "Hello [NAME],\n\n"
        "Thank you for your interest in modular kitchen and furniture. Our "
        "Durian Full Home Customisation is just the place for you.\n\n"
        "Please visit https://www.durian.in/full-home-customisation and fill in "
        "your details. Our Design Experts will contact you within 24 hours.\n\n"
        "Please mail us at customersupport@durian.in or call our Design Experts "
        "at 1800 209 3242.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_expensive_fhc": (
        "Hello [NAME],\n\n"
        "As our solutions are tailored to your unique needs, we'd like to give "
        "you the most accurate quote. Book your appointment today and connect "
        "with our Design Experts to discuss your design requirements in "
        "detail.\n\n"
        "Contact us at 1800 209 3242 to book your appointment.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_greeting": (
        "Hello [NAME],\n\n"
        "How can we help you?\n\n"
        "Regards,\nTeam Durian"
    ),
    "social_recruitment": (
        "Hello [NAME],\n\n"
        "Thank you for your interest. Kindly share your resume at "
        "recruit@durian.in and our team will get back to you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_collaboration": (
        "Hello [NAME],\n\n"
        "Thank you for your interest and for sharing your contact details.\n\n"
        "In case we have any requirement, our executives will surely get in "
        "touch with you.\n\n"
        "Regards,\nTeam Durian."
    ),

    # ── Public comment replies (short — for IG/FB comments, not DMs) ───────
    "social_comment_redirect_to_dm": (
        "Hello [NAME],\n\n"
        "Thank you for your interest. Kindly check your DM for the product "
        "details and price.\n\n"
        "We look forward to hearing from you.\n\n"
        "Regards,\nTeam Durian."
    ),
    "social_comment_praise": (
        "We're thrilled you liked it! There's more where that came from 💫 "
        "Take a closer look: www.durian.in and visit us at: www.durian.in/stores"
    ),
    "social_comment_product_mention": (
        "Thank you! Our pieces are designed for both style and everyday living "
        "🤍 Explore the full collection here: www.durian.in and visit us at: "
        "www.durian.in/stores"
    ),
    "social_comment_intent_to_visit": (
        "We can't wait to welcome you! Let us know if you'd like help locating "
        "your nearest store 🛋️ Visit us at: www.durian.in/stores"
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
