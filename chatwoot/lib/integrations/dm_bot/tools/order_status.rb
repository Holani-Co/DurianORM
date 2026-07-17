# frozen_string_literal: true

# Demo order lookup. There is no real order backend yet, so this returns a
# plausible status for well-formed demo IDs and a "check the ID" nudge
# otherwise — mirroring how the old prompt faked order handling.
class Integrations::DmBot::Tools::OrderStatus < Integrations::DmBot::Tools::Base
  def self.name
    'order_status'
  end

  description 'Check the delivery status of a customer order by its order ID.'

  param :order_id, desc: 'The order ID to look up (e.g. DEMO-12345)', required: true
  param :reason, desc: 'One short sentence: why you are checking this order', required: true

  def execute(order_id:, **)
    id = order_id.to_s.strip
    return "No order found for #{id}. Ask the customer to double-check the order ID." unless id.match?(/\A#?DEMO-\d+/i)

    "Order #{id}: shipped and out for delivery, ETA 2-3 days (carrier: BlueDart)."
  end
end
