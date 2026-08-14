# Agent-mode skills — generated from social_agent.SKILLS

Every runtime tool, its arguments, return shape, and one example. Regenerate via `python -c 'import social_agent; social_agent.write_skills_md()'` (a test asserts freshness).

## search_products

Look up Durian products on the LIVE durian.in storefront — the website's own search, so results are current sellable products at live prices (handles customer vocabulary: 'L-shaped' finds sectional sofas). USE WHEN a customer names or describes a product. QUERY = the customer's product NOUNS ONLY ('centre table', 'fabric sofa') — never adjectives or prices: 'premium', 'luxury', 'better', 'around ₹40,000' are NOISE to the keyword engine and pull in wrong-category rows. Quality and budget are the PRICE AXIS instead: better/premium/upmarket → same noun query + sort='price_desc'; cheaper/budget → 'price_asc'; a stated budget → min_price/max_price around it (₹40,000 → 30000–50000). Default order ranks CHEAP first, so never call a product range 'our best' without a price_desc look. Each row carries its category — rows OUTSIDE the asked category are misses, not suggestions (never offer a nesting table for a centre-table ask): re-query once with different nouns, then say honestly what the range is. Quote a product WITH its link — the link previews the product and its page carries every photo, so do not send photos separately unless the customer asks or you are comparing. EMI exists on everything: a one-line 'EMI options available' needs no fetch, but any EMI figure requires get_emi_plans first. Empty products[] = not found: rephrase and retry ONCE, then say so honestly, never invent.

**Args**: `{"query": {"type": "string", "description": "the customer's product nouns \u2014 no adjectives, no prices"}, "sort": {"type": "string", "enum": ["", "price_desc", "price_asc"], "description": "price_desc for better/premium asks, price_asc for budget asks; \"\" (default order) for a first neutral look"}, "min_price": {"type": "number", "description": "rupees, with max_price brackets a stated budget"}, "max_price": {"type": "number", "description": "rupees cap"}}`
**Returns**: `{"products": "list of {title, category, price, mrp?, link, note?} \u2014 prices pre-formatted in Indian notation, quote them verbatim; mrp present only when the price is a discount off it", "note": "str \u2014 set when there is something to relay honestly"}`
**Example**: `{"query": "centre table", "sort": "price_desc"}` → `{"products": [{"title": "Marissa", "category": "Coffee & Center Tables", "price": "\u20b965,430", "mrp": "\u20b91,45,400", "link": "https://www.durian.in/product/marissa-brown-veneer-solid-wood-coffee-&-center-table"}]}`

## get_emi_plans

Snapmint EMI plan details for a product (sku/family) or a price in rupees. Call ONLY when the customer asks about EMI/instalments — never volunteer plan figures with a product quote (a one-line 'EMI options available' mention needs no fetch). MANDATORY before quoting ANY EMI figure — tenure, monthly amount, down payment — every single time, even when plans were quoted in an earlier turn (always re-fetch; history is not current truth). Quote returned numbers EXACTLY, digit for digit. error set → EMI unavailable, say so, never invent plans. Side effect: tags the conversation emi-enquiry.

**Args**: `{"sku": {"type": "string"}, "price": {"type": "number"}}`
**Returns**: `{"product": "str", "price": "\u20b9-formatted str", "down_payment": "\u20b9-formatted str", "plans": "list of {months, emi_per_month, zero_cost, total_payment, interest} \u2014 all amounts pre-formatted, quote verbatim"}`
**Example**: `{"sku": "ESMERALDA"}` → `{"product": "MARBLE DINING SET 1+6", "price": "\u20b92,04,880", "plans": [{"months": 6, "emi_per_month": "\u20b934,146", "zero_cost": true}]}`

## find_showrooms

Resolve the customer's location to Durian showrooms. PINCODE FIRST — a pincode resolves to exactly ONE nearest showroom (never ask for a city while holding a pincode; when a city gives several options, ask for their pincode instead of reciting the list). address_message carries the store FACTS — showroom name, manager, 📞 phone, 🗺️ map link: copy those exactly into your own message when they want the store details; its letter dressing (Dear Customer / Regards) is not content and never pastes in.

**Args**: `{"pincode": {"type": "string"}, "city": {"type": "string"}}`
**Returns**: `{"resolved": "bool", "showroom": "str", "city": "str", "options": "list[str] when city has several \u2014 ask for pincode", "address_message": "store facts (manager, phone, map link) \u2014 copy the facts exactly, the framing is yours", "note": "guidance when not resolved"}`
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

Send product photos ONLY when the customer explicitly asks to see photos, or when comparing shortlisted products — never with an ordinary quote: the listing link in your reply already previews the product and its page carries every photo. Every photo sent is that variant's FRONT view. Default: one photo per variant (up to 3 variants, site order; a single-variant product gets two photos). Customer named a colour/size → pass it as `variant` so that photo leads. Comparing two products → call once per product with compare=true (exactly one front view each). Photos go once per product per conversation; a later call for the same family delivers only a variant not yet pictured (pass `variant`) — unless resend=true, which you set ONLY when the customer explicitly asks to see the photos again. DMs only: in a public comment thread this refuses — invite them to DM. After calling, include the returned listing link in your text reply so they can tap through.

**Args**: `{"family": {"type": "string", "description": "catalog family, e.g. BENJAMIN CORNER-I"}, "variant": {"type": "string", "description": "colour/size words the customer used, e.g. 'camel brown' or '3 seater'"}, "compare": {"type": "boolean", "description": "true when comparing products \u2014 exactly one front-view photo of this family"}, "resend": {"type": "boolean", "description": "true ONLY when the customer explicitly asked to see already-sent photos again"}}`
**Returns**: `{"sent": "int \u2014 photos delivered", "link": "listing URL for your reply", "variants": "list of variant names sent", "note": "str"}`
**Example**: `{"family": "MEAGAN", "variant": "camel brown"}` → `{"sent": 1, "link": "https://www.durian.in/product/meagan-camel-brown-\u2026", "variants": ["Camel Brown Premium Leatherette 2 Seater Sofa"]}`

## visualize_in_room

Generate a preview of a Durian product placed in the customer's OWN room photo. ALWAYS CALL FIRST — never ask the customer about colour or placement preemptively: this skill LOOKS at their room photo, and when the room makes placement obvious (one same-type piece → it gets replaced) no question is needed; you ask ONLY when a denial says so. PRECONDITIONS (all enforced in code): the customer has completed an enquiry (phone + showroom routing), has sent a room photo in this conversation, and is within the daily preview limit. Pass `variant` when the customer named or previously discussed one, and `placement` when they said where it should go. Denials return `denied` with what to do: need_enquiry → collect their details via the normal flow first; need_photo → ask for a photo of their space; need_variant / need_placement → ask exactly the ONE question in the note, then call again with their answer (never more than these two questions in total); daily_cap → tell them our sales team will prepare more mock-ups and escalate_to_human. Some products have only fabric-swatch photos on file — then the skill declines and you offer the showroom instead. When generation starts, the skill itself tells the customer it will take about 2 minutes — never repeat that promise in your reply. Every preview is indicative — say so.

**Args**: `{"family": {"type": "string"}, "variant": {"type": "string", "description": "colour/size the customer wants visualized, if named or previously discussed"}, "placement": {"type": "string", "description": "where IN the room, only when the customer actually said it \u2014 'replace my current sofa', 'by the window'. NEVER generic phrases like 'in my room'"}}`
**Returns**: `{"sent": "bool", "denied": "one of need_enquiry|need_photo|need_variant|need_placement|daily_cap|unavailable", "note": "what to do next"}`
**Example**: `{"family": "VERONICA", "variant": "canary yellow", "placement": "replace the current sofa"}` → `{"sent": true}`

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
