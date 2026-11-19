# Email routing — two-level taxonomy (Category → Subcategory)

Source of truth: **"Durian ORM Working Team – Feedback"** sheet, tabs `Executive Detail`,
`Email Template`, `Email - Keywords`. This spec captures the agreed design; the concrete config
is drafted in [`zoho-bridge/routing_rules.proposed.yaml`](routing_rules.proposed.yaml)
(a **proposal**, not the live `routing_rules.yaml`).

## 1. The change

Today the classifier picks **one category** (department) and forwards to one owner. The client now
routes several categories by a **second level — the product vertical**. Not every category has a
second level.

```
Level 1  CATEGORY      what the email is about
Level 2  SUBCATEGORY   which product line (only for the 3 vertical-aware categories)

Product Enquiry ┐
Complaint       ├─► vertical ∈ { retail_furniture · full_home_customization ·
Franchise       ┘                 ecom · doors_veneer_plywood · laminate }

Project/Bulk Order ─► sector ∈ { government · private }     (already implemented)
Everything else    ─► flat, one owner, no Level 2
```

**Decisions (client-confirmed):**
- **doors + veneer + plywood = one vertical** (`doors_veneer_plywood`).
- **laminate = separate vertical** → external vendor **Cedar India** (`rsenapati@cedarindia.com`).
- **Ambiguous vertical → agent decision card.** The model never guesses furniture-vs-doors etc.;
  it posts a decision card (same UX as the existing Govt/Private sector prompt) and the agent
  picks the vertical. Routing resumes from that choice.
- **Executive emails in `Executive Detail` are the internal forward targets** (they supersede the
  live `pratik.ojha` / `rohit.kanoujia`).
- **General Information = in-channel only on email.** The city/store template (from Rachael) is
  **Instagram/Facebook only**, never the email path.

## 2. Full routing matrix

| Category | Subcategory | Forward TO (team) | Team CC / BCC | Customer in CC | Ack |
|---|---|---|---|:--:|:--:|
| Product Enquiry | retail_furniture | *CRM/showroom matrix* | — | no | ack only |
| | doors_veneer_plywood | *CRM/showroom matrix* | — | no | ack only |
| | full_home_customization | hofhc.mis@durian.in | — | no | ack only |
| | ecom | sushil.k@durian.in | — | no | ack only |
| | laminate | rsenapati@cedarindia.com, laminates@durian.in | — | no | ack only |
| General Information | — | *(in-channel; no forward)* | — | no | ack only |
| Existing Order | — | *(BMS order-lookup; Abhishek Pandey)* | — | no | ack only |
| Complaint | retail_furniture | customersupport@durian.in | CC Jeevan.jyoti@durian.in | **YES** | yes |
| | full_home_customization | nishita.nayak@durian.in, hofhc.mis@durian.in | — | no | yes |
| | ecom | abhishek.pandey@durian.in | — | no | yes |
| | doors_veneer_plywood | anshu.tiwari@durian.in | — | no | yes |
| | laminate | rsenapati@cedarindia.com | — | no | yes |
| Legal Complaint | legal_notice | escalation@durian.in | CC customersupport@durian.in · BCC Jeevan.jyoti@durian.in | **YES** | yes |
| | grievance_form | tsen@durian.in | — (none) | no | yes |
| Franchise/Dealership | retail_furniture / FHC | ram.sai@durian.in | — | no | yes |
| | ecom | shilpa@durian.in | — | no | yes |
| | doors_veneer_plywood | anshu.tiwari@durian.in | CC rohit.kanoujia@durian.in | no | yes |
| | laminate | rsenapati@cedarindia.com, laminates@durian.in | — | no | yes |
| Vendor/Supplier | — | ganguli@durian.in | CC shivshankar.kushwah@durian.in | no | yes |
| Marketing/Advertising | — | shilpa@durian.in | — | no | yes |
| Collaboration | — | snehal@durian.in | — | no | yes |
| Career/Job | — | hrcoordinator@durian.in | — | **YES** | yes |
| Project/Bulk Order | govt / private | marketing@durian.in / *matrix* | — | no | yes |
| **Finance related** *(new)* | — | dipesh.patel@durian.in | — | no | yes |
| **Customer Technology** *(new)* | — | varsha@durian.in | — | no | yes |
| **Digital Marketing/SEO** *(new)* | — | lav.agarwal@durian.in | — | no | yes |

### CC rules distilled to two flags
- `acknowledge_customer` = **TRUE for every category** (customer always gets an ack).
- `include_customer_in_cc` = **TRUE for exactly three**: Complaint·retail_furniture, Legal·legal_notice,
  Career. Everywhere else FALSE → private forward to the team + a separate customer ack.
- Extra recipients are plain `cc` / `bcc` on the row (Jeevan, customersupport, rohit, shivshankar).

Model: **"customer in CC" = one email, forwarded with the customer copied.** "Not in CC" = two
emails — a private team forward + a separate customer acknowledgment.

## 3. Templates (`Email Template` tab)

**Template 1 — Acknowledgment / "your query was forwarded"** — used whenever we forward to a team.
Placeholders `[Customer Name]` · `[Department Name]` · `[Executive Email ID]`. The
`[Executive Email ID]` sentence ("kindly connect with them directly at …") is shown **only when
`share_executive_email` is true** for that (category, subcategory); otherwise the same body runs
without it.

**Template 2 — New Product Enquiry auto-reply** — the retail Product Enquiry opener. Placeholder
`[Customer Name]`. Collects **Name / Contact / City / Pincode** (feeds the retail city→showroom→owner
matrix), links durian.in, phone 9920116000, hours 10:30 AM–6:00 PM.

Selection: Product Enquiry → Template 2; every forwarded category → Template 1 (with/without the
exec-email sentence per `share_executive_email`).

## 4. Implementation shape

- **Config** — nested `subcategories:` under each vertical-aware category, each carrying
  `forward_to` / `cc` / `bcc` / `include_customer_in_cc` / `share_executive_email`; a `default:`
  per category for the fallback; `on_ambiguous_subcategory: agent_decision_card`. Flat categories
  unchanged. See the proposed YAML.
- **Classifier** — extend structured output from `{category}` to `{category, subcategory}`;
  subcategory enum constrained to the category's allowed verticals (`""`/none for flat categories),
  validated post-hoc (retry on invalid pair). Reuses the retail-gate vertical detection, generalized.
  Default vertical = `retail_furniture` unless a doors/FHC/ecom/laminate signal fires; if still
  ambiguous on a vertical-aware category → decision card.
- **Routing** — `resolve(category, subcategory)`: if the category has that subcategory, use it; else
  `default`; else flat category (today's behavior).
- **Decision card** — reuse the existing decision-panel mechanism (Govt/Private sector) with vertical
  options.
- **Backwards-compat** — categories without `subcategories:` behave exactly as today. Only Product
  Enquiry / Complaint / Franchise (+ Bulk sector) gain Level 2.

## 5. Keyword ingestion (`Email - Keywords` → taxonomy)

565 keywords extracted. Column → node:

| Col | Node | Col | Node |
|---|---|---|---|
| A | career_job_enquiry | I | project_bulk_order · private |
| B | complaint (223 kw) | J | vertical · full_home_customization |
| C | legal_complaint | K | vertical · doors_veneer_plywood |
| D | collaboration_request | L | existing_order_enquiry / ecom |
| E | marketing_advertising | **M** | **finance_related (new)** |
| F | vendor_supplier_enquiry | **N** | **customer_technology (new)** |
| G | franchise_dealership | **O** | **digital_marketing_seo (new)** |
| H | project_bulk_order · government | P | fallback → hello@durian.in |

- Col B (complaint) is grouped by defect type (Product Defects, Delivery, Installation, Service,
  Replacement, Warranty) — all stay **one category**; the vertical inside comes from J/K/L, not the
  defect type.
- Cols H/I are detection **patterns** (govt-entity vs private-company signals), not literal keywords
  — they seed the sector classifier's description/examples.
- Ram Sai (col G header) = **Franchise only**, not a vendor path.

## 6. Open items before code
1. **`share_executive_email` rule** — currently set to the 4 rows marked YES in col G
   (ProductEnquiry·ecom, ProductEnquiry·laminate, Complaint·doors_veneer_plywood, Complaint·laminate).
   Confirm it's those exact rows vs a rule (e.g. enquiries yes / complaints no).
2. Confirm the two new templates supersede the per-row `Sample response mail` bodies in col N (or
   which wins where both exist).
