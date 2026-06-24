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
end
