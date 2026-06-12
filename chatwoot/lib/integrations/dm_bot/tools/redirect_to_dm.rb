# frozen_string_literal: true

# Decision tool for PUBLIC comments: move the conversation to DMs instead of
# answering publicly. Like Handoff, the `rule` param ties the redirect to the
# brand-safe comment rule that triggered it, so the trace shows *why* it
# redirected rather than answered.
class Integrations::DmBot::Tools::RedirectToDm < Integrations::DmBot::Tools::Base
  def self.name
    'redirect_to_dm'
  end

  description 'On a PUBLIC comment, redirect the person to DMs instead of answering publicly. ' \
              'Use for any question, price/stock/order query, or complaint on a comment thread.'

  param :rule, desc: 'Why redirect — one of: price_question, order_query, complaint, needs_back_and_forth', required: true
  param :reason, desc: 'One short sentence explaining why this should move to a DM', required: true

  def execute(**)
    'Redirect registered — the public reply will point them to DMs.'
  end
end
