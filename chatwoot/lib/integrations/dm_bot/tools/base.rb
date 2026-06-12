# frozen_string_literal: true

require 'ruby_llm'

# Base class for DM Bot tools. Turning the bot's single LLM call into a
# tool-calling loop is what produces a truthful chain-of-thought: every step in
# the trace is a real tool the model chose to call (with its own stated reason),
# not a post-hoc narration. Shared catalog/shipping data lives here so the
# product tools stay in sync with what the order tool can sell.
class Integrations::DmBot::Tools::Base < RubyLLM::Tool
  CATALOG = [
    { sku: 'AOT-V1',   title: 'Attack on Titan — Vol. 1 (English)',     price: 499 },
    { sku: 'OP-V1',    title: 'One Piece — Vol. 1 (English)',           price: 549 },
    { sku: 'DS-BOX',   title: 'Demon Slayer — Complete Box Set (1-23)', price: 8_999 },
    { sku: 'NRT-V1',   title: 'Naruto — Vol. 1 (English)',              price: 449 },
    { sku: 'BRK-DLX1', title: 'Berserk — Deluxe Edition Vol. 1',        price: 2_499 }
  ].freeze

  SHIPPING = 'Flat ₹99 across India. Free over ₹2,000. Delivery in 4-7 days.'
  RETURNS  = '7-day return window, item must be unopened.'
end
