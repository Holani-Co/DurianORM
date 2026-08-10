# Durian — ORM Overview report.
#
# A single-screen "health board" for the Durian ORM: how much of the inbound
# the AI handled on its own, how much still needs a person, and what business
# it generated (deals, tickets) plus the review pulse — all for a date range.
#
# Everything here reads data the bridge already writes (labels, conversation
# custom_attributes, the ai_auto_reply marker on messages, review attributes),
# so there is no new capture — this just surfaces it.
class V2::Reports::OrmOverviewBuilder
  include V2::Reports::OrmMetrics

  # Human names for the inbox channel types Durian actually runs. Anything not
  # listed falls back to the demodulised class name (e.g. Channel::Foo → Foo).
  CHANNEL_NAMES = {
    'Channel::Instagram' => 'Instagram',
    'Channel::FacebookPage' => 'Facebook',
    'Channel::Whatsapp' => 'WhatsApp',
    'Channel::Email' => 'Email',
    'Channel::WebWidget' => 'Live chat',
    'Channel::Api' => 'API',
    'Channel::TwilioSms' => 'SMS'
  }.freeze

  def initialize(account:, params:)
    @account = account
    @params = params
  end

  # Labels the bridge tags conversations with, reused as the drill-through
  # target for the matching tile so the number and the list always agree.
  DEAL_LABEL = 'deal-created'
  TICKET_LABEL = 'zoho-ticket'
  AGENT_NEEDED_LABEL = 'agent-needed'

  EMI_LABEL = 'emi-enquiry'

  def build
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      conversations: conversations_summary,
      ai: ai_summary,
      first_response: { avg_seconds: avg_first_response_for(range) },
      deals: { created: label_count(DEAL_LABEL) },
      tickets: { raised: label_count(TICKET_LABEL) },
      emi: { enquiries: label_count(EMI_LABEL) },
      reviews: reviews_summary,
      categories: category_mix,
      # Same metrics over the immediately-preceding period of equal length, so
      # every tile can show whether it's up or down. Keyed to the tile names.
      previous: period_numbers(prev_range),
      # Which label each drillable tile filters on, so the UI can deep-link the
      # exact conversations behind the number.
      drilldowns: {
        deals: DEAL_LABEL,
        tickets: TICKET_LABEL,
        emi: EMI_LABEL,
        agent_needed: AGENT_NEEDED_LABEL
      }
    }
  end

  private

  attr_reader :account, :params

  def conversations_in_range
    @conversations_in_range ||= account.conversations.where(created_at: range)
  end

  # Total inbound conversations started in the period, plus a per-channel split.
  def conversations_summary
    by_channel = conversations_in_range
                 .joins(:inbox)
                 .group('inboxes.channel_type')
                 .count
                 .transform_keys { |ct| CHANNEL_NAMES.fetch(ct, ct.to_s.split('::').last) }
    { total: conversations_in_range.count, by_channel: by_channel }
  end

  # AI workload: public replies the bot sent on its own, how many conversations
  # that covered, and how many are currently still waiting on a human.
  def ai_summary
    auto_reply_messages = account.messages
                                 .where(created_at: range)
                                 .where("content_attributes ->> 'source' = 'ai_auto_reply'")
    {
      auto_replies_sent: auto_reply_messages.count,
      auto_handled_conversations: auto_reply_messages.distinct.count(:conversation_id),
      agent_needed_open: label_count(AGENT_NEEDED_LABEL, only_open: true)
    }
  end

  def avg_first_response_for(on_range)
    avg = ReportingEvent.where(account_id: account.id, name: 'first_response', created_at: on_range).average(:value)
    avg ? avg.to_f.round : 0
  end

  # The period of equal length immediately before the selected range.
  def prev_range
    @prev_range ||= begin
      duration = range.end - range.begin
      (range.begin - duration)..range.begin
    end
  end

  # The numeric tile values for a period, used for the previous-period deltas.
  def period_numbers(on_range)
    auto = account.messages.reorder(nil)
                  .where(created_at: on_range)
                  .where("content_attributes ->> 'source' = 'ai_auto_reply'")
    {
      conversations: account.conversations.where(created_at: on_range).count,
      auto_handled: auto.distinct.count(:conversation_id),
      auto_replies: auto.count,
      deals: label_count(DEAL_LABEL, on_range: on_range),
      tickets: label_count(TICKET_LABEL, on_range: on_range),
      emi: label_count(EMI_LABEL, on_range: on_range),
      reviews: reviews_count_for(on_range),
      first_response: avg_first_response_for(on_range)
    }
  end

  # Count of reviews posted in a period (by their actual posting date).
  def reviews_count_for(on_range)
    account.conversations.where("additional_attributes ->> 'type' = 'google_review'").find_each.count do |conv|
      posted = safe_time((conv.additional_attributes || {})['review_created_at'])
      posted && on_range.cover?(posted)
    end
  end

  # Google reviews bucketed by their ACTUAL posting date (review_created_at, a
  # string in additional_attributes) — the same rule the reviews CSV uses. A
  # few hundred review conversations, so filter/average in Ruby rather than
  # risk a bad cast aborting the query.
  def reviews_summary
    convs = account.conversations.where("additional_attributes ->> 'type' = 'google_review'")
    distribution = Hash.new(0)
    stars = []
    convs.find_each do |conv|
      attrs = conv.additional_attributes || {}
      posted = safe_time(attrs['review_created_at'])
      next if posted.nil? || !range.cover?(posted)

      s = attrs['stars'].to_i
      distribution[s] += 1
      stars << s if s.positive?
    end
    {
      count: stars.size,
      avg_stars: stars.empty? ? 0 : (stars.sum.to_f / stars.size).round(2),
      distribution: (1..5).index_with { |s| distribution[s] }
    }
  end

  # Enquiry mix — the classifier's category on each conversation in the period.
  def category_mix
    conversations_in_range
      .where("custom_attributes ->> 'email_category' IS NOT NULL")
      .group("custom_attributes ->> 'email_category'")
      .count
  end

  def safe_time(value)
    return nil if value.blank?

    Time.zone.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end
end
