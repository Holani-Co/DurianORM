# WhatsApp Furniture flow — a deterministic menu bot for the FURNITURE WhatsApp
# number, mirroring the durian.in website chatbot (Engati). Same state-machine
# pattern as whatsapp_fhc.py: greet → list menu → branch. State lives in the
# conversation's custom_attributes.wa_furniture.
#
# INBOX-SCOPED + dark-launched: main.py only calls handle() when the inbox is
# WHATSAPP_FURNITURE_INBOX_ID and WHATSAPP_FURNITURE_FLOW_ENABLED is true.
#
# Spec: docs/whatsapp-furniture-flow-spec.md. Branches marked TODO are pending
# the client inputs in spec §8 (offer tag/URLs, store dataset, shop-in-store
# URLs, product routing confirmations) — they hand off to a human until built.

import config
import chatwoot

_GREETING = "Welcome to Durian! 👋 What can we help you with today?"

# WhatsApp list rows (send_interactive_buttons renders >3 items as a native
# WhatsApp list). Order/labels taken from the website chatbot's main menu.
_MAIN_MENU = [
    {"title": "Explore Offers", "value": "offers"},
    {"title": "SALE LIVE NOW", "value": "sale"},
    {"title": "Find Stores near you", "value": "store"},
    {"title": "Talk to a Durian Expert", "value": "expert"},
    {"title": "Track your Order", "value": "track"},
    {"title": "Shop In-Store", "value": "shop"},
]

_MENU_PROMPT = "Please choose from the options below 👇"


def _choice(text: str) -> str | None:
    """Map a menu tap (Chatwoot delivers the button/list title or value) to a
    branch key."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if any(k in t for k in ("offer", "sale")):
        return "offers"
    if "store" in t or "showroom" in t or "near you" in t:
        return "store"
    if "expert" in t or "talk to" in t:
        return "expert"
    if "track" in t or "order" in t:
        return "track"
    if "shop" in t or "in-store" in t or "in store" in t:
        return "shop"
    return None


async def handle(conv: dict, conv_id: int, latest_message: str = "",
                 latest_msg_id=None) -> dict | None:
    """Advance the furniture flow one step. main.py gates on the inbox + enable
    flag before calling. Returns a handled/ignored dict, or None to fall through
    to existing behaviour."""
    ca = conv.get("custom_attributes") or {}
    st = dict(ca.get("wa_furniture") or {})
    if latest_msg_id is not None and st.get("last_msg") == latest_msg_id:
        return {"ignored": True, "reason": "wa_furniture_already_handled"}

    text = (latest_message or "").strip()
    step = st.get("step")

    async def _save(**kw) -> None:
        st.update(kw)
        st["last_msg"] = latest_msg_id
        await chatwoot.merge_custom_attributes(conv_id, {"wa_furniture": st})

    async def _say(msg: str) -> None:
        await chatwoot.send_outgoing_message(conv_id, msg)

    async def _menu(prompt: str = None) -> None:
        await chatwoot.send_interactive_buttons(conv_id, prompt or _GREETING, _MAIN_MENU)

    # Fresh intent after completion → reopen the menu.
    if step == "done":
        st.clear()
        await _menu()
        await _save(step="menu", tries=0)
        return {"handled": "wa_furniture_reengaged"}

    # First contact → greet + menu.
    if not step:
        await _menu()
        await _save(step="menu", tries=0)
        return {"handled": "wa_furniture_greeted"}

    # Menu → route.
    if step == "menu":
        choice = _choice(text)
        if choice:
            return await _route(choice, conv, conv_id, st, _save, _say)
        tries = int(st.get("tries") or 0) + 1
        if tries >= 2:
            await _handoff(conv_id, "WhatsApp Furniture: customer didn't pick a menu option")
            await _say("Let me connect you with our team — they'll assist you shortly 🙏")
            await _save(step="done", tries=tries)
            return {"handled": "wa_furniture_menu_handoff"}
        await _menu(_MENU_PROMPT)
        await _save(tries=tries)
        return {"handled": "wa_furniture_menu_reprompt"}

    # Mid-branch steps dispatch back into the branch handlers.
    return await _route(st.get("branch") or _choice(text), conv, conv_id, st, _save, _say)


async def _route(branch, conv, conv_id, st, _save, _say) -> dict:
    """Enter/continue a branch. Each branch is implemented in its own coroutine;
    unimplemented ones hand off to a human (see spec §8)."""
    handlers = {
        "offers": _branch_offers,
        "store": _branch_store,
        "expert": _branch_expert,
        "track": _branch_track,
        "shop": _branch_shop,
    }
    fn = handlers.get(branch)
    if not fn:
        await _say("Let me connect you with our team — they'll assist you shortly 🙏")
        await _save(step="done")
        return {"handled": "wa_furniture_unknown_branch"}
    return await fn(conv, conv_id, st, _save, _say)


# ── Branches ───────────────────────────────────────────────────────────────
# TODO(spec §8): implement each once the client inputs are confirmed. For now
# they acknowledge and hand off so nothing misbehaves under dark-launch.

async def _branch_offers(conv, conv_id, st, _save, _say) -> dict:
    # TODO: send live furniture-vertical offer (Offers feature, chatwoot.get_offers
    # by furniture tag) + "ENDS SOON" link; else offers-page URL; then
    # "get in touch?" → lead. Needs: offer tag, offers-page URL.
    await _say("Thanks! Our team will share the latest Durian offers with you shortly 🙏")
    await _save(step="done", branch="offers")
    return {"handled": "wa_furniture_offers_stub"}


async def _branch_store(conv, conv_id, st, _save, _say) -> dict:
    # TODO: pincode → nearest furniture showroom (+distance, map link, Enquire).
    # Needs: furniture showroom dataset with coordinates for distance.
    await _say("Please share your *pincode* 📍 and our team will send your nearest "
               "Durian Furniture showroom details.")
    await _save(step="done", branch="store")
    return {"handled": "wa_furniture_store_stub"}


async def _branch_expert(conv, conv_id, st, _save, _say) -> dict:
    # TODO: name → phone → pincode → product → CRM deal (product routing per
    # spec §5). Needs: product-list scope, wardrobes routing, support-owner path.
    await _say("Great! Our Durian expert will reach out to you shortly 🙂 "
               "Could you share your *name* and *pincode*?")
    await _save(step="done", branch="expert")
    return {"handled": "wa_furniture_expert_stub"}


async def _branch_track(conv, conv_id, st, _save, _say) -> dict:
    # TODO: Via Phone / Via Order ID → bms.py lookup → order card → More Actions
    # (complaint → Desk ticket / enquiry). bms.py is available; mostly buildable.
    await _say("Sure! Please share your *Order ID* to track your order 📦")
    await _save(step="done", branch="track")
    return {"handled": "wa_furniture_track_stub"}


async def _branch_shop(conv, conv_id, st, _save, _say) -> dict:
    # TODO: nav links to durian.in sections. Needs: exact behaviour + URLs.
    await _say("You can explore and shop Durian furniture on durian.in 🛋️ "
               "Our team will help if you need anything specific.")
    await _save(step="done", branch="shop")
    return {"handled": "wa_furniture_shop_stub"}


async def _handoff(conv_id: int, note: str) -> None:
    """Best-effort: leave a private note so an agent picks it up."""
    try:
        await chatwoot.create_message(conv_id, note, message_type="outgoing", private=True)
    except Exception as e:
        print(f"[wa-furniture] handoff note failed for conv {conv_id}: {e}")
