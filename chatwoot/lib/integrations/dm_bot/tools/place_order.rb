# frozen_string_literal: true

# Places a demo order once the model has conversationally collected all the
# required details. Returns a confirmation in the same shape the old prompt
# produced, so the customer-facing format is unchanged.
class Integrations::DmBot::Tools::PlaceOrder < Integrations::DmBot::Tools::Base
  def self.name
    'place_order'
  end

  description 'Place an order. Only call once you have collected: product(s) + quantity, ' \
              'full name, shipping address with PIN code, phone number, and the total.'

  param :items, desc: 'Items and quantities, e.g. "1x Naruto Vol.1 (NRT-V1)"', required: true
  param :customer_name, desc: 'Customer full name', required: true
  param :shipping_address, desc: 'Full shipping address including PIN code', required: true
  param :phone, desc: 'Contact phone number', required: true
  param :total, desc: 'Order total in ₹ including shipping, as a number', required: true
  param :reason, desc: 'One short sentence: why you are placing the order now', required: true

  # name/address/phone/reason are collected and logged via the trace; the demo
  # confirmation only echoes items + total, so the rest is absorbed by **.
  def execute(items:, total:, **)
    order_id = "DEMO-#{rand(10_000..99_999)}"
    "✅ Order placed! Order ID: ##{order_id}\nItems: #{items}\nTotal: ₹#{total}\nETA: 4-7 days"
  end
end
