# frozen_string_literal: true

require 'ruby_llm'

# Base class for DM Bot tools. Turning the bot's single LLM call into a
# tool-calling loop is what produces a truthful chain-of-thought: every step in
# the trace is a real tool the model chose to call (with its own stated reason),
# not a post-hoc narration.
#
# Durian is a lead-gen + support flow (not transactional): the bot answers
# enquiries with the right link, invites the customer to share contact details
# for an executive to follow up, and hands off serious issues. So the shared
# data here is Durian's contact points + catalog links — the facts the DM
# prompt is allowed to state directly — not a sellable product catalog.
class Integrations::DmBot::Tools::Base < RubyLLM::Tool
  SUPPORT_PHONE  = '1800 209 3242'
  WHATSAPP       = '8591108987'
  SUPPORT_EMAIL  = 'customersupport@durian.in'
  RECRUIT_EMAIL  = 'recruit@durian.in'
  STORE_LOCATOR  = 'https://www.durian.in/stores'

  # Where the bot points customers per vertical. Prices are NOT listed — Durian
  # never quotes price over DM (it varies by requirement); the bot shares the
  # link + collects contact instead.
  LINKS = {
    furniture: 'https://www.durian.in/catalog/durian-furniture-collection-2020',
    bedroom: 'https://www.durian.in/buy-furniture/bedroom-furniture',
    door: 'https://www.durian.in/catalog/retail-door-catalog-2020',
    wardrobe: 'https://www.durian.in/wardrobe',
    fhc: 'https://www.durian.in/full-home-customisation'
  }.freeze

  # Durian doors retail is only available in these cities.
  DOOR_CITIES = 'Indore, Bangalore, Delhi, Mumbai & Hyderabad'

  # ── Demo-store data (kisnemanga persona only) ──────────────────────────────
  # Used by the restored demo tools (search_catalog / order_status / place_order)
  # and the kisnemanga DM prompt, for demo accounts mapped via
  # DM_BOT_PERSONA_MAP. Not referenced anywhere in the Durian flow.
  CATALOG = [
    { sku: 'AOT-V1',   title: 'Attack on Titan — Vol. 1 (English)',     price: 499 },
    { sku: 'OP-V1',    title: 'One Piece — Vol. 1 (English)',           price: 549 },
    { sku: 'DS-BOX',   title: 'Demon Slayer — Complete Box Set (1-23)', price: 8_999 },
    { sku: 'NRT-V1',   title: 'Naruto — Vol. 1 (English)',              price: 449 },
    { sku: 'BRK-DLX1', title: 'Berserk — Deluxe Edition Vol. 1',        price: 2_499 }
  ].freeze

  SHIPPING = 'Flat ₹99 across India. Free over ₹2,000. Delivery in 4-7 days.'
  RETURNS  = '7-day return window, item must be unopened.'
  PAYMENT  = 'UPI, debit/credit cards, net banking, popular wallets, and Cash on Delivery (COD).'
end
