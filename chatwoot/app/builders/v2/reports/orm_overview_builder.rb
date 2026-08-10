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

  # Labels the bridge tags conversations with, reused as the drill-through
  # target for the matching tile so the number and the list always agree.
  DEAL_LABEL = 'deal-created'
  TICKET_LABEL = 'zoho-ticket'
  AGENT_NEEDED_LABEL = 'agent-needed'

  def build
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      conversations: conversations_summary,
      ai: ai_summary,
      first_response: { avg_seconds: avg_first_response_seconds },
      deals: { created: label_count(DEAL_LABEL) },
      tickets: { raised: label_count(TICKET_LABEL) },
      reviews: reviews_summary,
      categories: category_mix,
      # Which label each drillable tile filters on, so the UI can deep-link the
      # exact conversations behind the number.
      drilldowns: {
        deals: DEAL_LABEL,
        tickets: TICKET_LABEL,
        agent_needed: AGENT_NEEDED_LABEL
      }
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
      agent_needed_open: label_count(AGENT_NEEDED_LABEL, only_open: true)
    }
  end

  # Count of conversations tagged with `label`. Scoped to conversations started
  # in the range, except the agent-needed queue which is a live "now" count of
  # what's still open — so the tile matches the label view the tile drills into.
  def label_count(label, only_open: false)
    conversation_scope = { account_id: account.id }
    if only_open
      conversation_scope[:status] = Conversation.statuses[:open]
    else
      conversation_scope[:created_at] = range
    end
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN conversations ON taggings.taggable_id = conversations.id')
      .joins('INNER JOIN tags ON taggings.tag_id = tags.id')
      .where(taggable_type: 'Conversation', context: 'labels')
      .where(tags: { name: label })
      .where(conversations: conversation_scope)
      .count
  end

  def avg_first_response_seconds
    avg = ReportingEvent.where(account_id: account.id, name: 'first_response', created_at: range).average(:value)
    avg ? avg.to_f.round : 0
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
