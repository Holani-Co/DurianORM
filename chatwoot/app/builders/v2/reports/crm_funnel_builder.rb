# Durian — CRM / Lead funnel report.
#
# The sales journey the ORM generates: product enquiries routed to a showroom →
# qualified (phone + city captured, ready for a deal) → deal created in Zoho
# CRM. Plus the deal mix by vertical and by enquiry category. Runs on the
# labels the bridge tags along the retail/deal flow.
class V2::Reports::CrmFunnelBuilder
  include V2::Reports::OrmMetrics

  ENQUIRY_LABEL = 'retail-routed'
  QUALIFIED_LABEL = 'deal-ready'
  DEAL_LABEL = 'deal-created'

  # Per-vertical deal labels the bridge applies (mirrors _DEAL_VERTICAL_LABEL
  # in the bridge), shown as the deal mix.
  VERTICAL_LABELS = {
    'Bulk / Project' => 'deal-bulk',
    'Full home' => 'deal-fhc',
    'Doors' => 'deal-doors',
    'Product' => 'deal-product',
    'Franchise' => 'deal-franchise'
  }.freeze

  def initialize(account:, params:)
    @account = account
    @params = params
  end

  def build
    enquiries = label_count(ENQUIRY_LABEL)
    qualified = label_count(QUALIFIED_LABEL)
    deals = label_count(DEAL_LABEL)
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      funnel: [
        { stage: 'Enquiries routed', count: enquiries, label: ENQUIRY_LABEL },
        { stage: 'Qualified', count: qualified, label: QUALIFIED_LABEL },
        { stage: 'Deals created', count: deals, label: DEAL_LABEL }
      ],
      conversion_rate: enquiries.positive? ? (deals * 100.0 / enquiries).round : 0,
      by_vertical: VERTICAL_LABELS.transform_values { |label| label_count(label) },
      by_category: deals_by_category
    }
  end

  private

  attr_reader :account, :params

  # Conversation ids tagged deal-created within the range.
  def deal_conversation_ids
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN conversations ON taggings.taggable_id = conversations.id')
      .joins('INNER JOIN tags ON taggings.tag_id = tags.id')
      .where(taggable_type: 'Conversation', context: 'labels')
      .where(tags: { name: DEAL_LABEL })
      .where(conversations: { account_id: account.id, created_at: range })
      .pluck(Arel.sql('conversations.id'))
  end

  # The deals broken down by the enquiry category they came from.
  def deals_by_category
    ids = deal_conversation_ids
    return {} if ids.empty?

    account.conversations
           .where(id: ids)
           .where("custom_attributes ->> 'email_category' IS NOT NULL")
           .group("custom_attributes ->> 'email_category'")
           .count
  end
end
