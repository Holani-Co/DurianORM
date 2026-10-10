# WhatsApp Furniture Flow — Spec

**Status:** Draft for sign-off
**Owner:** Aditya
**Target:** New deterministic WhatsApp menu bot for the **furniture WhatsApp number** (+91 82918 55015, WABA `657622220274451`, inbox created in ORM), mirroring Durian's website chatbot (Engati) flow.

---

## 1. Goal & principle

Give the furniture WhatsApp number the same self-serve flow the website chatbot offers: a **button/list menu** that handles offers, store locator, retail enquiry (→ CRM), order tracking (→ BMS), complaints/enquiries (→ Zoho Desk), and shop-in-store navigation.

This is almost entirely **wiring existing bridge subsystems** into one deterministic flow — not new subsystems. It mirrors the existing **`whatsapp_fhc.py`** state-machine pattern.

**Non-goals:** free-text/NLP AI answering (the website bot is menu-driven; we stay deterministic), and any change to the current live WhatsApp number.

---

## 2. Scope & gating

- **New module:** `zoho-bridge/whatsapp_furniture.py` (mirrors `whatsapp_fhc.py`: per-conversation state in the conversation's `additional_attributes.wa_furniture`).
- **Inbox-scoped:** runs **only** on the furniture inbox — new env `WHATSAPP_FURNITURE_INBOX_ID`. The current number and all other inboxes are untouched. Dispatched from `main.handle_message_created` alongside the FHC gate.
- **Dark-launched:** `WHATSAPP_FURNITURE_FLOW_ENABLED` (default off). When off, `whatsapp_furniture.handle` returns `None` and the conversation falls through to existing behaviour.
- **Channel:** WhatsApp Cloud (interactive **list** for the 6-item main menu; **reply buttons** ≤3 for sub-steps).

---

## 3. Main menu

Greeting: *"Welcome to Durian! What can we help you with today?"* → WhatsApp **list**:

| # | Row | Branch |
|---|-----|--------|
| 1 | Explore Offers | §4.1 Offers |
| 2 | SALE LIVE NOW | §4.1 Offers (same as Explore Offers) |
| 3 | Find the Stores near you | §4.2 Store locator |
| 4 | Talk to a Durian Expert | §4.3 Retail enquiry → CRM |
| 5 | Track your Order | §4.4 Order tracking → BMS |
| 6 | Shop In-Store | §4.5 Navigation |

After every branch completes, offer **🏠 Main Menu** to loop back (the WhatsApp equivalent of the website's persistent Main Menu / Store / Consultation / Catalogue nav).

---

## 4. Branch specs

### 4.1 Explore Offers / SALE LIVE NOW
1. Send the live **furniture-vertical offer** (image + caption + "ENDS SOON" link).
   - **Reuse:** Offers feature (`OFFERS_ENABLED`, offer-by-tag, `_pick_offer_llm`).
   - If no live/unexpired furniture offer → send the **furniture offers page URL** (fallback).
2. Then: *"Would you like us to get in touch with you?"* **[Yes] [No]**
   - **Yes** → collect **name** + confirm **phone** → create a CRM lead/deal (retail, location-routed per §5) + confirm.
   - **No** → back to Main Menu.

### 4.2 Find the Stores near you
1. Ask **pincode**.
2. Resolve **nearest Durian Furniture showroom**.
   - **Reuse:** `pincode_resolver` + showroom dataset (`retail_showrooms` / `fhc_stores`).
3. Send store card: photo, city, distance ("Varanasi · 6 kms"), **Show on Google Maps** (URL), **Enquire** (→ lead/CRM per §5).

### 4.3 Talk to a Durian Expert (retail enquiry → CRM)
Collect in order:
1. **Name** (default = WhatsApp profile name; confirm/override).
2. **Phone** (default = the WhatsApp number; confirm or "use another").
3. **Location pincode.**
4. **Product** (list): Furniture · Plywood · Doors · Veneers · Laminates · Wardrobes.

→ Create a **retail CRM deal** with the details; **owner/routing depends on the product** (see §5). Confirm to the customer.
- **Reuse:** `zoho_crm.py` deal creation + owner matrix / two-level routing.

### 4.4 Track your Order (BMS)
1. *"How would you like to track your order?"* **[Via Phone Number] [Via Order ID]**
   - Via Order ID → ask Order ID → `bms.py get-orders-by-order-id`.
   - Via Phone Number → use the WhatsApp number (or ask) → `bms.py get-orders-by-customer`.
   - **Reuse:** `bms.py` (already live for order lookup).
2. Send order card: Order ID, created date, amount, item count, city, **Status**.
3. **More Actions** → *"Raise a Complaint or Put an Enquiry"*:
   - **Raise a Complaint** → *"Can you please explain your complaint?"* (free text) → create **Zoho Desk ticket** (§6) → confirm.
   - **Put an Enquiry** → capture enquiry → CRM/enquiry note (or Desk, per §6).

### 4.5 Shop In-Store (navigation)  — _TBD, see §8_
Nav rows linking to durian.in sections (catalogue / book a store visit / consultation). Exact destinations + URLs to be confirmed.

### 4.6 Talk to an agent / callback (cross-cutting)
Wherever the customer wants a human, capture the enquiry → **Zoho Desk ticket tagged to Customer Support** for a callback (§6).

---

## 5. CRM product routing (Talk to a Durian Expert)

The selected product decides the CRM deal's owner/routing:

| Product | CRM routing |
|---------|-------------|
| **Furniture** | Retail deal, owner routed by **location** (existing retail routing) |
| **Wardrobes** | Retail deal, location-routed _(confirm: retail vs FHC/Home-Studio)_ |
| **Doors** | Doors vertical routing (existing) |
| **Laminates** | **Existing CRM flow** routing (laminate vertical) |
| **Veneers** | Tag/route to **Customer Support** in CRM |
| **Plywood** | Tag/route to **Customer Support** in CRM |

All deals carry the customer's name / phone / pincode and the chosen product as the vertical.

---

## 6. Zoho Desk routing

- **Complaints** (from Track Order → More Actions → Raise a Complaint, and any complaint intent) → **Zoho Desk ticket** with the description → **Customer Support** department.
- **Callbacks** ("Talk to an agent" / "Enquire") → Zoho Desk ticket tagged to **Customer Support** with "please call back" + the enquiry.
- **Reuse:** `zoho.py` ticket creation; existing approval toggle (`ZOHO_TICKET_REQUIRE_APPROVAL`) applies.

---

## 7. New config / env

| Env | Purpose |
|-----|---------|
| `WHATSAPP_FURNITURE_FLOW_ENABLED` | Dark-launch flag (default off) |
| `WHATSAPP_FURNITURE_INBOX_ID` | The furniture inbox id (flow runs only here) |
| `FURNITURE_OFFERS_URL` | Fallback URL for the furniture offers page (when no live offer) |
| `WHATSAPP_FURNITURE_OFFER_TAG` | Offer tag/vertical identifying furniture offers _(or reuse existing tagging)_ |
| Shop-In-Store URLs | Catalogue / consultation / store nav links (§4.5) |

Reused as-is: `OFFERS_ENABLED`, CRM owner matrix / two-level routing, `zoho.py` Desk config, `bms.py` BMS config, `pincode_resolver` data.

---

## 8. Open items to confirm

1. **Shop In-Store** — exact behaviour + the durian.in URLs (catalogue? book visit? consultation?).
2. **Offers** — the furniture **offer tag**, the **"ENDS SOON" link**, and the **offers-page fallback URL**.
3. **Wardrobes routing** — retail (location) or FHC/Home-Studio?
4. **Store data** — confirm furniture showrooms are in the pincode/showroom dataset with distance (website shows "6 kms"); our resolver returns nearest — verify distance is available.
5. **Furniture inbox id** for `WHATSAPP_FURNITURE_INBOX_ID`.
6. **Main-menu labels/order** — confirm the 6 rows and wording.

---

## 9. Rollout plan

1. **Sign off** this spec + fill §8.
2. Build `whatsapp_furniture.py` (menu + branches), inbox-scoped, dark-launched — reusing Offers / pincode / CRM / Desk / BMS.
3. Enable on the furniture number only; **test each branch** end-to-end (offer, store, expert→CRM deal, track→BMS, complaint→Desk).
4. Iterate on copy/labels, then leave enabled.

**Reuse summary:** menu state machine ← `whatsapp_fhc.py`; offers ← Offers feature; store ← `pincode_resolver`; CRM ← `zoho_crm.py`; Desk ← `zoho.py`; order tracking ← `bms.py`. New code is mostly the flow wiring + the furniture menu.
