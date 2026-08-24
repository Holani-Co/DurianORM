# WhatsApp Full Home Customisation (FHC) flow — a deterministic button-menu
# bot for the FHC WhatsApp inbox. Unlike the Instagram/Facebook LLM agent, this
# is a fixed state machine: greet → 3-button menu → collect details → route.
#
#   Product enquiry → name → phone → pincode → nearest of the 7 FHC studios:
#       in coverage  → create the Home Studio deal (owner = that studio)
#       outside      → tag Customer Support
#   Store address   → city/pincode → send that studio's store card
#   Other help      → capture the ask → flag agent-needed, a human takes over
#
# State lives in conversation custom_attributes["wa_fhc"] = {step, choice, name,
# phone, ...}. Dark-launched behind WHATSAPP_FHC_FLOW_ENABLED. The dispatch hook
# in main.handle_message_created only calls this for the WhatsApp inbox when the
# conversation isn't owned by a human.

import re
from datetime import datetime, timezone

import chatwoot
import config
import fhc_stores
import pincode_resolver

_GREETING = (
    "Hello Sir/Ma'am 👋 Thank you for your interest in *Durian Full Home "
    "Customisation* 🏠\n\n"
    "Explore our collection here:\n"
    "https://www.durian.in/full-home-collection/collections\n\n"
    "How can we help you today?"
)

_MENU = [
    {"title": "Product enquiry", "value": "product"},
    {"title": "Store address", "value": "store"},
    {"title": "Other help", "value": "other"},
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _choice(text: str) -> str | None:
    """Map a menu tap (Chatwoot delivers the button title/value as the incoming
    text) OR a typed reply to one of the three options."""
    t = (text or "").strip().lower()
    if any(k in t for k in ("product", "enquir", "buy", "purchase")):
        return "product"
    if any(k in t for k in ("store", "address", "location", "showroom", "studio")):
        return "store"
    if any(k in t for k in ("other", "help", "something else")):
        return "other"
    return None


def _extract_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if len(digits) == 10 and digits[0] in "6789" else ""


def _first_name(full: str) -> str:
    return (full or "").strip().split()[0] if (full or "").strip() else "there"


def _sender_phone(conv: dict) -> str:
    """The contact's own number, normalized to a 10-digit Indian mobile. On
    WhatsApp this is the number they're messaging from (so we can skip asking);
    on the website widget it's usually blank (so we ask)."""
    raw = ((conv.get("meta") or {}).get("sender") or {}).get("phone_number") or ""
    return _extract_phone(raw)


_CONFIRM_PHONE = [{"title": "Yes, use this", "value": "phone_yes"},
                  {"title": "Use another", "value": "phone_no"}]

# What the customer wants to customise. >3 items → the widget/WhatsApp render
# this as a tappable LIST. Maps to a CRM field once the client gives its API
# name; until then it rides in the deal Description + a private note.
_INTEREST = [{"title": "Kitchen", "value": "kitchen"},
             {"title": "Wardrobe", "value": "wardrobe"},
             {"title": "Full Home", "value": "full_home"},
             {"title": "TV Unit", "value": "tv_unit"},
             {"title": "Other storage", "value": "other_storage"}]
_INTEREST_LABEL = {"kitchen": "Kitchen", "wardrobe": "Wardrobe",
                   "full_home": "Full Home", "tv_unit": "TV Unit",
                   "other_storage": "Other storage"}


def _match_interest(text: str) -> str:
    t = (text or "").strip().lower()
    for key, kws in (("full_home", ("full home", "full_home", "whole home", "entire home")),
                     ("kitchen", ("kitchen",)),
                     ("wardrobe", ("wardrobe", "closet", "almirah")),
                     ("tv_unit", ("tv", "entertainment")),
                     ("other_storage", ("other", "storage"))):
        if any(k in t for k in kws):
            return key
    return "unspecified"


async def _flag_agent(conv_id: int, reason: str, *, support: bool = False) -> None:
    """Hand the conversation to a human. `support=True` also labels it for the
    Customer Support queue (product enquiry outside the studio network)."""
    labels = ["agent-needed", "agent-needed-whatsapp"]
    if support:
        labels.append("customer-support")
    for lbl in labels:
        try:
            await chatwoot.add_label(conv_id, lbl)
        except Exception:
            pass
    try:
        await chatwoot.post_private_note(conv_id, f"⚠️ {reason} — needs a human.")
    except Exception:
        pass
    if config.SOCIAL_AGENT_HANDOFF_TEAM_ID:
        try:
            await chatwoot.assign_team(conv_id, config.SOCIAL_AGENT_HANDOFF_TEAM_ID)
        except Exception:
            pass


async def _create_fhc_deal(conv_id: int, name: str, phone: str, pincode: str,
                           store: dict, interest: str = "") -> bool:
    """Record the resolved studio + create the FHC (Home Studio) deal via the
    shared _create_crm_deal (which handles the CRM's mandatory custom fields).
    Never blocks the customer: on any failure the conversation is flagged so a
    human completes the deal from the Create-Deal button.

    NB: _create_crm_deal routes the OWNER by city, so the exact owner assignment
    (esp. Mumbai: Goregaon vs Thane, same city) needs live verification against
    the CRM before this branch is switched on — the precise studio + owner_id we
    resolved is written to attributes + a private note either way.
    """
    interest_label = _INTEREST_LABEL.get(interest, "")
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "deal_customer_details": {"phone": phone, "city": store["city"],
                                      "pincode": pincode, "interest": interest,
                                      "captured_at": _now_iso()},
            "phase2_category": "full_home_customization",
            "fhc_studio": store["location"],
            "fhc_interest": interest,
            "retail_customer_phone": phone,
        })
        await chatwoot.post_private_note(
            conv_id,
            f"🧾 WhatsApp FHC enquiry — {name} · {phone} · pincode {pincode}"
            + (f"\nLooking to customise: {interest_label}" if interest_label else "")
            + f"\nNearest studio: {store['location']} (CRM owner {store['owner_id']}).")
        import main  # lazy import — avoids a circular import at module load
        await main._create_crm_deal(conv_id, agent_name="WhatsApp FHC bot",
                                    sector="full_home_customization", phone=phone)
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[wa-fhc] deal auto-create deferred to human for conv {conv_id}: {e}")
        await _flag_agent(conv_id, f"WhatsApp FHC deal auto-create failed ({e})")
        return False


async def _create_support_deal(conv_id: int, name: str, phone: str, pincode: str,
                               interest: str = "") -> bool:
    """Out-of-coverage FHC lead → create the deal assigned to Customer Support
    (config.FHC_SUPPORT_OWNER_ID), so no lead is invisible to the CRM. No-op
    (returns False) when that owner isn't configured — the caller keeps the
    Chatwoot handoff. Never blocks the customer."""
    if not config.FHC_SUPPORT_OWNER_ID:
        return False
    interest_label = _INTEREST_LABEL.get(interest, "")
    try:
        await chatwoot.merge_custom_attributes(conv_id, {
            "deal_customer_details": {"phone": phone, "pincode": pincode,
                                      "interest": interest, "captured_at": _now_iso()},
            "phase2_category": "full_home_customization",
            "fhc_interest": interest,
            "fhc_out_of_coverage": True,
            "retail_customer_phone": phone,
        })
        await chatwoot.post_private_note(
            conv_id,
            f"🧾 WhatsApp FHC enquiry (OUTSIDE studio network) — {name} · {phone} "
            f"· pincode {pincode}"
            + (f"\nLooking to customise: {interest_label}" if interest_label else "")
            + "\nRouted to Customer Support in the CRM.")
        import main  # lazy import — avoids a circular import at module load
        await main._create_crm_deal(conv_id, agent_name="WhatsApp FHC bot",
                                    sector="full_home_customization", phone=phone,
                                    owner_id_override=config.FHC_SUPPORT_OWNER_ID,
                                    owner_label="Customer Support")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[wa-fhc] support deal create failed for conv {conv_id}: {e}")
        return False


async def handle(conv: dict, conv_id: int, latest_message: str = "",
                 latest_msg_id=None) -> dict | None:
    """Advance the FHC flow one step. Shared by the WhatsApp and website-widget
    dispatch hooks — each gates on its own enable flag BEFORE calling, so this
    just runs the flow. Returns a handled/ignored dict. The wa_fhc conversation
    attribute holds state on both channels (the widget renders input_select the
    same way WhatsApp does, so the same buttons + state machine work as-is)."""
    ca = conv.get("custom_attributes") or {}
    st = dict(ca.get("wa_fhc") or {})
    if latest_msg_id is not None and st.get("last_msg") == latest_msg_id:
        return {"ignored": True, "reason": "wa_fhc_already_handled"}

    text = (latest_message or "").strip()
    step = st.get("step")

    async def _save(**kw) -> None:
        st.update(kw)
        st["last_msg"] = latest_msg_id
        await chatwoot.merge_custom_attributes(conv_id, {"wa_fhc": st})

    async def _say(msg: str) -> None:
        await chatwoot.send_outgoing_message(conv_id, msg)

    # Flow finished → the human (or the customer's next fresh intent) owns it.
    if step == "done":
        return {"ignored": True, "reason": "wa_fhc_done"}

    # ── First contact → greet + menu ────────────────────────────────────────
    if not step:
        await chatwoot.send_interactive_buttons(conv_id, _GREETING, _MENU)
        await _save(step="menu", tries=0)
        return {"handled": "wa_fhc_greeted"}

    # ── Menu → route on the tapped/typed choice ─────────────────────────────
    if step == "menu":
        choice = _choice(text)
        if choice == "product":
            await _say("Great! To register your enquiry, may I have your *name*? 🙂")
            await _save(step="p_name", choice="product")
        elif choice == "store":
            await _say("Sure! Please share your *city or area pincode* 📍 and I'll "
                       "send you your nearest Durian studio's details.")
            await _save(step="s_pin", choice="store")
        elif choice == "other":
            await _say("No problem! Please tell us what you need help with, and our "
                       "team will assist you shortly 😊")
            await _save(step="other", choice="other")
        else:
            tries = int(st.get("tries") or 0) + 1
            if tries >= 2:
                await _flag_agent(conv_id, "WhatsApp FHC: customer didn't pick a menu option")
                await _say("Let me connect you with our team — they'll assist you shortly 🙏")
                await _save(step="done", tries=tries)
                return {"handled": "wa_fhc_menu_handoff"}
            await chatwoot.send_interactive_buttons(
                conv_id, "Please choose one of the options below 👇", _MENU)
            await _save(tries=tries)
        return {"handled": f"wa_fhc_menu_{choice or 'reprompt'}"}

    # ── Product enquiry: name → phone → pincode → deal ──────────────────────
    if step == "p_name":
        await chatwoot.send_interactive_buttons(
            conv_id,
            f"Thank you, {_first_name(text)}! What would you like to customise? 🏠",
            _INTEREST)
        await _save(step="p_interest", name=text[:80])
        return {"handled": "wa_fhc_p_name"}

    if step == "p_interest":
        interest = _match_interest(text)
        known = _sender_phone(conv)
        if known:
            # We already have their number (WhatsApp) — confirm instead of asking.
            await chatwoot.send_interactive_buttons(
                conv_id,
                f"Got it! Can we reach you on this same number ending "
                f"{known[-4:]}? 📞",
                _CONFIRM_PHONE)
            await _save(step="p_phone_confirm", interest=interest, known_phone=known)
        else:
            await _say("Got it! 📞 Please share your *phone number*.")
            await _save(step="p_phone", interest=interest)
        return {"handled": "wa_fhc_p_interest"}

    if step == "p_phone_confirm":
        t = text.strip().lower()
        if any(k in t for k in ("yes", "phone_yes", "use this", "same", "correct")):
            await _say("Great! 📍 Finally, your *area pincode* — so we connect you to "
                       "your nearest studio.")
            await _save(step="p_pin", phone=st.get("known_phone"))
        elif any(k in t for k in ("no", "phone_no", "another", "different", "other")):
            await _say("No problem — please share the *phone number* you'd like us to "
                       "use. 📞")
            await _save(step="p_phone")
        else:
            await chatwoot.send_interactive_buttons(
                conv_id, "Just to confirm — which number should we use? 📞",
                _CONFIRM_PHONE)
        return {"handled": "wa_fhc_p_phone_confirm"}

    if step == "p_phone":
        phone = _extract_phone(text)
        if not phone:
            await _say("That doesn't look like a valid number 🙈 Please share a "
                       "10-digit *phone number*.")
            return {"handled": "wa_fhc_p_phone_retry"}
        await _say("Thanks! 📍 Finally, your *area pincode* — so we connect you to "
                   "your nearest studio.")
        await _save(step="p_pin", phone=phone)
        return {"handled": "wa_fhc_p_phone"}

    if step == "p_pin":
        pin = pincode_resolver.extract_pincode(text) or pincode_resolver.normalize_pincode(text)
        if not pin:
            await _say("Please share a valid 6-digit *pincode* 📍")
            return {"handled": "wa_fhc_p_pin_retry"}
        name = st.get("name") or "there"
        store, dist = fhc_stores.nearest_store(pin)
        if store and dist is not None and dist <= fhc_stores.COVERAGE_KM:
            await _create_fhc_deal(conv_id, st.get("name") or "", st.get("phone") or "",
                                   pin, store, interest=st.get("interest") or "")
            await _say(f"Thank you, {_first_name(name)}! ✅ Your enquiry has been "
                       f"registered with our *{store['card_name']}* studio. Our team "
                       f"will reach out to you shortly.\n\nThank you for choosing Durian ✨")
            await _save(step="done", pincode=pin, store=store["location"])
            return {"handled": "wa_fhc_deal"}
        # Outside the studio network → create a Customer Support deal (if the
        # support owner is configured) AND hand off in Chatwoot.
        await _create_support_deal(conv_id, st.get("name") or "",
                                   st.get("phone") or "", pin, st.get("interest") or "")
        await _flag_agent(conv_id,
                          f"WhatsApp FHC: product enquiry outside studio network (pincode {pin})",
                          support=True)
        await _say(f"Thank you, {_first_name(name)}! Your enquiry has been shared with "
                   f"our *Customer Support* team, who will assist you further 🙏\n\n"
                   f"Thank you for choosing Durian ✨")
        await _save(step="done", pincode=pin, store="support")
        return {"handled": "wa_fhc_deal_support"}

    # ── Store address: pincode → nearest studio card ────────────────────────
    if step == "s_pin":
        pin = pincode_resolver.extract_pincode(text) or pincode_resolver.normalize_pincode(text)
        if not pin:
            await _say("Please share your *city or area pincode* 📍")
            return {"handled": "wa_fhc_s_pin_retry"}
        store, dist = fhc_stores.nearest_store(pin)
        if store and dist is not None and dist <= fhc_stores.COVERAGE_KM:
            await _say(fhc_stores.store_card(store))
        else:
            await _flag_agent(conv_id,
                              f"WhatsApp FHC: store request outside studio network (pincode {pin})")
            await _say("We don't have a Full Home Customisation studio near you yet 😔 "
                       "Our team will reach out to help you further 🙏")
        await _save(step="done", pincode=pin)
        return {"handled": "wa_fhc_store"}

    # ── Other help → capture the ask, hand to a human ───────────────────────
    if step == "other":
        await _flag_agent(conv_id, "WhatsApp FHC: 'Other help' request")
        try:
            await chatwoot.post_private_note(conv_id, f"💬 WhatsApp FHC — Other help: {text[:300]}")
        except Exception:
            pass
        await _say("Thank you! Our team has been notified and will assist you shortly 🙏")
        await _save(step="done")
        return {"handled": "wa_fhc_other"}

    return {"ignored": True, "reason": "wa_fhc_no_step"}
