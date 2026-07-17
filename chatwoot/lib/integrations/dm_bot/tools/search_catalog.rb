# frozen_string_literal: true

# Look up products before answering any product/price/availability question.
class Integrations::DmBot::Tools::SearchCatalog < Integrations::DmBot::Tools::Base
  def self.name
    'search_catalog'
  end

  description 'Look up products in the IComics / kisnemanga catalog by name or keyword. ' \
              'Call this before answering any product, price, or stock question.'

  param :query, desc: 'Product name or keyword to search for (e.g. "naruto", "box set")', required: true
  param :reason, desc: 'One short sentence: why you are searching the catalog right now', required: true

  # `reason` is filled by the model and captured from the tool call for the
  # trace; it isn't needed in the body, so it's absorbed by the anonymous **.
  def execute(query:, **)
    q = query.to_s.downcase.strip
    matches = CATALOG.select { |item| item[:title].downcase.include?(q) || item[:sku].downcase.include?(q) }
    matches = CATALOG if matches.empty?

    lines = matches.map { |i| "#{i[:title]} — ₹#{i[:price]} (SKU: #{i[:sku]})" }
    "#{lines.join("\n")}\n\nShipping: #{SHIPPING}\nReturns: #{RETURNS}"
  end
end
