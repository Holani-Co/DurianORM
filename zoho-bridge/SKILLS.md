# Agent-mode skills — generated from social_agent.SKILLS

Every runtime tool, its arguments, return shape, and one example. Regenerate via `python -c 'import social_agent; social_agent.write_skills_md()'` (a test asserts freshness).

## search_products

Look up Durian products. USE WHEN a customer names or describes a product (handles customer vocabulary: 'L-shaped' finds corner sofas). Returns DISTINCT products grouped by family with price ranges — present different products, never several finishes of one. Empty families[] = we could not find it: rephrase and retry ONCE, then say so honestly, never invent.

**Args**: `{"query": {"type": "string", "description": "product words from the customer"}}`
**Returns**: `{"families": "list of {family, name, price_from, price_to, variants} \u2014 prices pre-formatted in Indian notation, quote them verbatim", "price_period": "str \u2014 the price list month these prices come from"}`
**Example**: `{"query": "l shaped sofa"}` → `{"families": [{"family": "BENJAMIN CORNER", "name": "LEATHERETTE CORNER SOFA", "price_from": "\u20b91,20,480", "price_to": "\u20b91,44,900", "variants": 13}]}`

## get_emi_plans

Snapmint EMI plans for a product (sku/family) or a price in rupees. MANDATORY before ANY statement about EMI — availability included — every single time EMI/installments come up, even when plans were quoted in an earlier turn (always re-fetch; history is not current truth). Also use it to add a one-line EMI mention to a price quote. Quote returned numbers EXACTLY, digit for digit. error set → EMI unavailable, say so, never invent plans. Side effect: tags the conversation emi-enquiry.

**Args**: `{"sku": {"type": "string"}, "price": {"type": "number"}}`
**Returns**: `{"product": "str", "price": "\u20b9-formatted str", "down_payment": "\u20b9-formatted str", "plans": "list of {months, emi_per_month, zero_cost, total_payment, interest} \u2014 all amounts pre-formatted, quote verbatim"}`
**Example**: `{"sku": "ESMERALDA"}` → `{"product": "MARBLE DINING SET 1+6", "price": "\u20b92,04,880", "plans": [{"months": 6, "emi_per_month": "\u20b934,146", "zero_cost": true}]}`

## find_showrooms

Resolve the customer's location to Durian showrooms. PINCODE FIRST — a pincode resolves to exactly ONE nearest showroom (never ask for a city while holding a pincode; when a city gives several options, ask for their pincode instead of reciting the list). address_message is the customer-ready store card WITH the store phone number and map link — share it verbatim when they want the store details.

**Args**: `{"pincode": {"type": "string"}, "city": {"type": "string"}}`
**Returns**: `{"resolved": "bool", "showroom": "str", "city": "str", "options": "list[str] when city has several \u2014 ask for pincode", "address_message": "customer-ready store card (phone + map link)", "note": "guidance when not resolved"}`
**Example**: `{"pincode": "110054"}` → `{"resolved": true, "showroom": "Delhi - Kirti Nagar"}`

## route_to_showroom

Register a FURNITURE purchase enquiry with a showroom (bounded write — sets the deal owner your team's Create Deal uses; may auto-create the CRM deal when the checklist passes). USE ONCE when purchase intent is clear and location is unambiguous (a pincode, or city + explicit choice). Refuses ambiguity and re-routing.

**Args**: `{"pincode": {"type": "string"}, "city": {"type": "string"}, "showroom": {"type": "string"}}`
**Returns**: `{"routed": "bool", "showroom": "str", "deal_created": "bool", "options": "list[str] when ambiguous", "note": "str"}`
**Example**: `{"pincode": "110054"}` → `{"routed": true, "showroom": "Delhi - Kirti Nagar", "deal_created": true}`

## register_enquiry

Register a DOORS or FULL-HOME (FHC) purchase enquiry (bounded write — marks the deal category + customer details for your team's Create Deal). USE ONCE when the vertical is doors/FHC and you hold BOTH phone and city (a known pincode's city counts).

**Args**: `{"category": {"type": "string", "enum": ["doors", "fhc"]}, "phone": {"type": "string"}, "city": {"type": "string"}}`
**Returns**: `{"registered": "bool", "note": "str"}`
**Example**: `{"category": "doors", "phone": "9560150835", "city": "Delhi"}` → `{"registered": true}`

## share_offer

Check current offers and, when one fits, SEND it (image + caption) — at most once per conversation, enforced in code. USE on a first-contact greeting, or when discussing a product that has a matching offer (weave the offer into your price answer). Returns matched offers either way so you can mention the discount even when already shared.

**Args**: `{"product_context": {"type": "string", "description": "what the customer is interested in, if known"}}`
**Returns**: `{"sent": "bool", "offer_caption": "str", "matched": "list of captions", "note": "str"}`
**Example**: `{"product_context": "esmeralda dining set"}` → `{"sent": true, "offer_caption": "Festive 10% off dining sets\u2026"}`

## share_product_images

Send the customer photos of a product family they are interested in — one photo per variant (up to 3 variants; a single-variant product gets two photos). USE ONCE per product per conversation, whenever a customer shows real interest in a specific product. After calling, include the returned listing link in your text reply so they can tap through.

**Args**: `{"family": {"type": "string", "description": "catalog family, e.g. BENJAMIN CORNER-I"}}`
**Returns**: `{"sent": "int \u2014 photos delivered", "link": "listing URL for your reply", "variants": "list of variant names sent", "note": "str"}`
**Example**: `{"family": "MEAGAN"}` → `{"sent": 3, "link": "https://www.durian.in/product/meagan-\u2026", "variants": ["Mushroom Brown 2 Seater", "Grey 3 Seater"]}`

## visualize_in_room

Generate a preview of a Durian product placed in the customer's OWN room photo. PRECONDITIONS (all enforced in code): the customer has completed an enquiry (phone + showroom routing), has sent a room photo in this conversation, and is within the daily preview limit. Denials return `denied` with what to do: need_enquiry → collect their details via the normal flow first; need_photo → ask for a photo of their space; daily_cap → tell them our sales team will prepare more mock-ups and escalate_to_human. Every preview is indicative — say so.

**Args**: `{"family": {"type": "string"}, "variant": {"type": "string"}}`
**Returns**: `{"sent": "bool", "denied": "one of need_enquiry|need_photo|daily_cap|unavailable", "note": "what to do next"}`
**Example**: `{"family": "MEAGAN"}` → `{"sent": true}`

## escalate_to_human

Hand the conversation to a human (flags + assignment). USE for: order status / delivery / warranty, dealer or franchise, bulk / B2B / project, collabs, price negotiation, complaints beyond a first apology, abuse, or anything your tools cannot ground. If intent is UNCLEAR, ask ONE clarifying question first, THEN escalate with what you learned.

**Args**: `{"reason": {"type": "string"}, "customer_message": {"type": "string", "description": "one short courteous line to send the customer before handoff (optional)"}}`
**Returns**: `{"escalated": "bool"}`
**Example**: `{"reason": "franchise enquiry for Pune"}` → `{"escalated": true}`

## finish

End your turn. Compute confidence, never feel it: start 92; −20 per stated fact without a tool fetch this turn; −15 for a skipped/failed required action; −25 if intent is unclear. No subtraction → confidence 92, action send (intros and clarifying questions included — carding a clean reply is an error). Any subtraction → action card. `learned` is MANDATORY whenever the customer declined something, corrected a fact, stated a preference/budget/objection, or we promised follow-up THIS turn — each with the customer's exact words as `quote`. Empty `learned` after a decline/correction is an error. Example: [{"kind":"preference","what":"photos on WhatsApp","quote":"send photos on whatsapp only"},{"kind":"correction","field":"city","what":"Gurgaon","quote":"i have shifted to gurgaon"}]

**Args**: `action, reply, confidence, reasoning, learned[]`

## Customer profile schema

See `customer_profile.py` — event log (`t`, `msg`, `conv`, `inbox`, `kind`, `what`, `quote?`) + folded identity/location/commercial, consolidated stable_facts/episodes/transitions, linked_contacts (soft links, never merges).
