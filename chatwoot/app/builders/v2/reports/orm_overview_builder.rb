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

  def build
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      conversations: conversations_summary,
      ai: ai_summary,
      first_response: { avg_seconds: avg_first_response_seconds },
      deals: { created: deals_created_count },
      tickets: { raised: tickets_raised_count },
      reviews: reviews_summary,
      categories: category_mix
    }
  end

  private

  attr_reader :account, :params

  def range
    @range ||= begin
      since = Time.zone.at((params[:since].presence || 30.days.ago.to_i).to_i)
      till  = Time.zone.at((params[:until].presence || Time.current.to_i).to_i)
      since..till
    end
  end

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
      agent_needed_open: agent_needed_open_count
    }
  end

  # Live count of conversations currently tagged agent-needed and still open —
  # the "awaiting a person" queue (a now metric, not scoped to the range).
  def agent_needed_open_count
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN conversations ON taggings.taggable_id = conversations.id')
      .joins('INNER JOIN tags ON taggings.tag_id = tags.id')
      .where(taggable_type: 'Conversation', context: 'labels')
      .where(tags: { name: 'agent-needed' })
      .where(conversations: { account_id: account.id, status: Conversation.statuses[:open] })
      .count
  end

  def avg_first_response_seconds
    avg = ReportingEvent.where(account_id: account.id, name: 'first_response', created_at: range).average(:value)
    avg ? avg.to_f.round : 0
  end

  # Conversations that produced a CRM deal. Keyed on conversation start date as a
  # proxy — the deal-creation time isn't stamped separately on the conversation.
  def deals_created_count
    conversations_in_range.where("custom_attributes ->> 'crm_deal_id' IS NOT NULL").count
  end

  # Conversations that raised at least one Zoho ticket (current shape stores a
  # zoho_tickets array; a few legacy ones use the single zoho_ticket key).
  def tickets_raised_count
    conversations_in_range
      .where("jsonb_array_length(COALESCE(custom_attributes -> 'zoho_tickets', '[]'::jsonb)) > 0 " \
             "OR custom_attributes ? 'zoho_ticket'")
      .count
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
