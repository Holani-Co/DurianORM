# Durian — dated queries behind the ORM Monthly Report: WHAT happened WHEN.
# Every method takes a period and returns what the ORM did in it (India time),
# dated by when it happened — the moment a label was applied, a ticket raised,
# a review posted, a campaign message sent. Mixed into
# V2::Reports::OrmMonthlyReportBuilder, which turns these into the report.
module V2::Reports::OrmMonthlyQueries # rubocop:disable Metrics/ModuleLength
  TIME_ZONE = 'Asia/Kolkata'.freeze
  EMAIL_CHANNEL = 'Channel::Email'.freeze
  REVIEW_TYPES = %w[google_review website_review].freeze
  # Unwrapped message content_attributes (see V2::Reports::OrmMetrics).
  MESSAGE_ATTRS = V2::Reports::OrmMetrics::MESSAGE_ATTRS
  PRODUCT_LINES = { 'deal-product' => 'Furniture', 'deal-fhc' => 'Full Home',
                    'deal-doors' => 'Doors', 'deal-bulk' => 'Project / Bulk',
                    'deal-franchise' => 'Dealership / Franchise' }.freeze
  # Private note the bridge posts on every deal: "✅ CRM Deal created by <who> — …".
  DEAL_NOTE = /\A✅ CRM Deal created by (.+?) —/

  private

  def conversations(on_range)
    account.conversations.where(created_at: on_range)
  end

  def email_conversations(on_range)
    conversations(on_range).joins(:inbox).where(inboxes: { channel_type: EMAIL_CHANNEL })
  end

  # This account's conversation-label taggings for `names`.
  def label_taggings(names)
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN tags ON tags.id = taggings.tag_id')
      .joins('INNER JOIN conversations ON conversations.id = taggings.taggable_id')
      .where(taggable_type: 'Conversation', context: 'labels', tags: { name: names },
             conversations: { account_id: account.id })
  end

  # [{conv_id, tag, at}] — each time one of `names` was put on a conversation.
  def tag_events(names, on_range)
    memo(:tags, names, on_range) do
      label_taggings(names).where(taggings: { created_at: on_range })
                           .pluck('taggings.taggable_id', 'tags.name', 'taggings.created_at')
                           .map { |conv_id, tag, at| { conv_id: conv_id, tag: tag, at: at.in_time_zone(TIME_ZONE) } }
    end
  end

  # Email conversations whose category was decided in the period, with how it
  # was decided: :auto (AI), :by_agent (decision card) or :uncategorised.
  def classified_emails(on_range)
    memo(:classified, on_range) do
      rows = account.conversations.joins(:inbox)
                    .where(inboxes: { channel_type: EMAIL_CHANNEL })
                    .where("conversations.custom_attributes -> 'email_category_v2' IS NOT NULL")
                    .where('conversations.created_at < ? AND conversations.updated_at >= ?', on_range.end, on_range.begin)
                    .pluck(:id, :custom_attributes, :created_at)
      rows.filter_map do |id, attrs, created_at|
        category = attrs['email_category_v2'] || {}
        next unless on_range.cover?(parse_time(category['classified_at']) || created_at)

        { id: id, category: category_name(category), subcategory: humanize(category['vertical']),
          mode: classification_mode(category) }
      end
    end
  end

  # Every Zoho Desk ticket raised in the period (a conversation can hold several).
  def ticket_entries(on_range)
    memo(:tickets, on_range) do
      convs = account.conversations
                     .where("conversations.custom_attributes -> 'zoho_tickets' IS NOT NULL " \
                            "OR conversations.custom_attributes -> 'zoho_ticket' IS NOT NULL")
                     .where('conversations.updated_at >= ?', on_range.begin)
                     .preload(:contact, :inbox)
      convs.flat_map { |conv| tickets_of(conv) }.select { |t| on_range.cover?(t[:at]) }.sort_by { |t| t[:at] }
    end
  end

  def tickets_of(conv)
    attrs = conv.custom_attributes || {}
    list = attrs['zoho_tickets'].presence || [attrs['zoho_ticket']].compact
    Array(list).select { |ticket| ticket.is_a?(Hash) }.map { |ticket| ticket_row(ticket, conv, attrs) }
  end

  def ticket_row(ticket, conv, attrs)
    { at: parse_time(ticket['created_at']) || conv.created_at, number: ticket['number'] || ticket['id'],
      subject: ticket['subject'], status: ticket['status'], source: ticket['source'],
      category: category_name(attrs['email_category_v2'] || {}),
      customer: conv.contact&.name, channel: channel_label(conv.inbox&.channel_type) }
  end

  def deal_contact(conv, attrs)
    contact = conv.contact
    { customer: contact&.name, email: contact&.email,
      mobile: attrs['retail_customer_phone'].presence || contact&.phone_number }
  end

  def product_line(conv)
    tag = (conv.cached_label_list_array & PRODUCT_LINES.keys).first
    attrs = conv.custom_attributes || {}
    PRODUCT_LINES[tag] || humanize(attrs['phase2_category'].presence || (attrs['email_category_v2'] || {})['category'])
  end

  # conversation id → who created its deal, from the bridge's audit note.
  def deal_authors(on_range)
    account.messages.where(private: true, created_at: on_range)
           .where('messages.content LIKE ?', '✅ CRM Deal created by %')
           .pluck(:conversation_id, :content)
           .each_with_object({}) { |(conv_id, content), out| out[conv_id] ||= content[DEAL_NOTE, 1] }
  end

  # Google + website reviews by their actual posting date.
  def review_entries(on_range)
    memo(:reviews, on_range) do
      rows = account.conversations
                    .where("conversations.additional_attributes ->> 'type' IN (?)", REVIEW_TYPES)
                    .pluck(:additional_attributes, :cached_label_list)
      rows.filter_map do |attrs, labels|
        posted = parse_time(attrs['review_created_at'])
        next unless posted && on_range.cover?(posted)

        { type: attrs['type'], stars: attrs['stars'].to_i,
          replied: labels.to_s.split(',').map(&:strip).include?('review-replied') }
      end
    end
  end

  def ai_replies(on_range)
    account.messages.reorder(nil).where(created_at: on_range)
           .where("#{MESSAGE_ATTRS} ->> 'source' = 'ai_auto_reply'")
  end

  def open_label_count(label)
    label_taggings(label).where(conversations: { status: Conversation.statuses[:open] }).count
  end

  def conversations_by_id(ids)
    account.conversations.where(id: ids.uniq).preload(:contact, :inbox).index_by(&:id)
  end

  def classification_mode(category)
    return :uncategorised if category['category'].blank? || category['category'] == 'fallback'

    category['reason'] == 'Confirmed by agent.' ? :by_agent : :auto
  end

  def category_name(category)
    return 'Uncategorised' if category['category'].blank? || category['category'] == 'fallback'

    category['display_name'].presence || humanize(category['category'])
  end

  def channel_label(type)
    V2::Reports::OrmOverviewBuilder::CHANNEL_NAMES.fetch(type.to_s) { type.to_s.split('::').last.presence || 'Other' }
  end

  def humanize(key)
    key.to_s.tr('_', ' ').split.map(&:capitalize).join(' ').presence
  end

  def parse_time(value)
    return nil if value.blank?

    Time.zone.parse(value.to_s)&.in_time_zone(TIME_ZONE)
  rescue ArgumentError, TypeError
    nil
  end

  # Per-report cache: headline and detail sections reuse the same query result.
  def memo(*key)
    @memo ||= {}
    return @memo[key] if @memo.key?(key)

    @memo[key] = yield
  end
end
