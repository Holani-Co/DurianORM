# Durian — ORM Monthly Report (downloadable PDF + Excel for management).
#
# Everything the ORM did in one calendar month (India time), dated by WHEN IT
# HAPPENED: a deal counts in the month it was created, a ticket in the month it
# was raised, a forward in the month it was sent — so a closed month's numbers
# don't move later. (The on-screen ORM Overview dates by conversation start,
# so the two can differ slightly.)
#
# Reads only what the bridge already records (labels + their tagging time,
# conversation attributes, message markers, campaign deliveries) via
# V2::Reports::OrmMonthlyQueries. No new capture.
class V2::Reports::OrmMonthlyReportBuilder
  include V2::Reports::OrmMonthlyQueries

  PRODUCT_LINES = { 'deal-product' => 'Furniture', 'deal-fhc' => 'Full Home',
                    'deal-doors' => 'Doors', 'deal-bulk' => 'Project / Bulk',
                    'deal-franchise' => 'Dealership / Franchise' }.freeze
  FORWARD_LABELS = %w[auto-forwarded manually-sent].freeze
  FILTERED_INTENTS = %w[promotional automated spam].freeze
  SUGGESTION_TYPES = %w[ai_review_suggestion ai_order_reply].freeze
  # Private note the bridge posts on every deal: "✅ CRM Deal created by <who> — …".
  DEAL_NOTE = /\A✅ CRM Deal created by (.+?) —/
  AUTOMATIC_AUTHOR = /bridge|agent mode|bot/i

  attr_reader :account, :month_start, :range

  # month: 'YYYY-MM' (defaults to last month).
  def initialize(account:, month: nil)
    @account = account
    zone = ActiveSupport::TimeZone[TIME_ZONE]
    first = month.present? ? zone.parse("#{month}-01") : zone.now.prev_month
    @month_start = first.beginning_of_month
    @range = @month_start...@month_start.next_month
  end

  def build
    {
      month_label: month_start.strftime('%B %Y'),
      previous_label: previous_range.begin.strftime('%B %Y'),
      generated_at: Time.current.in_time_zone(TIME_ZONE),
      headline: headline(range),
      previous: headline(previous_range),
      emails: email_summary,
      deals: deal_summary,
      tickets: ticket_summary,
      social: social_summary,
      reviews: { google: review_summary('google_review'), website: review_summary('website_review') },
      campaigns: V2::Reports::OrmMonthlyCampaigns.new(account: account, range: range).summary
    }
  end

  private

  def previous_range
    month_start.prev_month...month_start
  end

  # The front-page numbers — also computed for the previous month so each can
  # show the month-over-month change.
  def headline(on_range)
    {
      conversations: conversations(on_range).count,
      emails: email_conversations(on_range).count,
      auto_classified: classified_emails(on_range).count { |c| c[:mode] == :auto },
      forwarded: tag_events(FORWARD_LABELS, on_range).size,
      deals: tag_events(%w[deal-created], on_range).size,
      tickets: ticket_entries(on_range).size,
      ai_replies: ai_replies(on_range).count,
      reviews: review_entries(on_range).size,
      campaign_delivered: V2::Reports::OrmMonthlyCampaigns.new(account: account, range: on_range).delivered_count
    }
  end

  # ── Emails ────────────────────────────────────────────────────────────────

  def email_summary
    classified = classified_emails(range)
    forwards = forward_rows
    {
      received: email_conversations(range).count,
      filtered: filtered_counts,
      classified: %i[auto by_agent uncategorised].index_with { |mode| classified.count { |c| c[:mode] == mode } },
      awaiting_review_now: open_label_count('needs-review'),
      forwarded: { auto: forwards.count { |f| f[:automatic] }, manual: forwards.count { |f| !f[:automatic] } },
      categories: category_rows(classified, forwards),
      forward_rows: forwards
    }
  end

  def filtered_counts
    intents = email_conversations(range).group("conversations.custom_attributes ->> 'email_category'").count
    FILTERED_INTENTS.index_with { |intent| intents[intent].to_i }
  end

  def forward_rows
    events = tag_events(FORWARD_LABELS, range)
    convs = conversations_by_id(events.pluck(:conv_id))
    rows = events.filter_map { |event| convs[event[:conv_id]] && forward_row(event, convs[event[:conv_id]]) }
    rows.sort_by { |row| row[:at] }
  end

  def forward_row(event, conv)
    category = (conv.custom_attributes || {})['email_category_v2'] || {}
    { at: event[:at], customer: conv.contact&.name, email: conv.contact&.email,
      category: category_name(category), subcategory: humanize(category['vertical']),
      automatic: event[:tag] == 'auto-forwarded' }
  end

  def category_rows(classified, forwards)
    forwarded = forwards.group_by { |f| f[:category] }.transform_values(&:size)
    rows = classified.group_by { |c| c[:category] }.map { |name, list| category_row(name, list, forwarded[name].to_i) }
    rows.sort_by { |row| -row[:total] }
  end

  def category_row(name, list, forwarded)
    { name: name, total: list.size, forwarded: forwarded,
      auto: list.count { |c| c[:mode] == :auto }, by_agent: list.count { |c| c[:mode] == :by_agent },
      subcategories: list.filter_map { |c| c[:subcategory] }.tally.sort_by { |_, n| -n }.to_h }
  end

  # ── Deals & tickets ───────────────────────────────────────────────────────

  def deal_summary
    events = tag_events(%w[deal-created], range)
    convs = conversations_by_id(events.pluck(:conv_id))
    authors = deal_authors
    rows = events.filter_map { |event| convs[event[:conv_id]] && deal_row(event, convs[event[:conv_id]], authors) }
    deal_breakdowns(rows.sort_by { |row| row[:at] })
  end

  def deal_breakdowns(rows)
    { total: rows.size, automatic: rows.count { |r| r[:automatic] }, rows: rows,
      by_product_line: tally(rows, :product_line), by_channel: tally(rows, :channel),
      by_showroom: tally(rows.select { |r| r[:showroom].present? }, :showroom).first(10).to_h }
  end

  def deal_row(event, conv, authors)
    attrs = conv.custom_attributes || {}
    author = authors[conv.id]
    { at: event[:at], **deal_contact(conv, attrs), product_line: product_line(conv),
      showroom: (attrs['retail_deal_owner'] || {})['location'], channel: channel_label(conv.inbox&.channel_type),
      created_by: author, automatic: author.to_s.match?(AUTOMATIC_AUTHOR), crm_deal_id: attrs['crm_deal_id'] }
  end

  def deal_contact(conv, attrs)
    contact = conv.contact
    { customer: contact&.name, email: contact&.email,
      mobile: attrs['retail_customer_phone'].presence || contact&.phone_number }
  end

  # conversation id → who created its deal, from the bridge's audit note.
  def deal_authors
    account.messages.where(private: true, created_at: range)
           .where('messages.content LIKE ?', '✅ CRM Deal created by %')
           .pluck(:conversation_id, :content)
           .each_with_object({}) { |(conv_id, content), out| out[conv_id] ||= content[DEAL_NOTE, 1] }
  end

  def product_line(conv)
    tag = (conv.cached_label_list_array & PRODUCT_LINES.keys).first
    attrs = conv.custom_attributes || {}
    PRODUCT_LINES[tag] || humanize(attrs['phase2_category'].presence || (attrs['email_category_v2'] || {})['category'])
  end

  def ticket_summary
    rows = ticket_entries(range)
    { total: rows.size, automatic: rows.count { |t| t[:source].to_s.casecmp('manual') != 0 },
      by_category: tally(rows, :category), rows: rows }
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

  # ── Conversations & AI ────────────────────────────────────────────────────

  def social_summary
    replies = ai_replies(range)
    drafts = ai_drafts
    {
      by_channel: channel_counts,
      ai_replies: replies.count,
      ai_conversations: replies.distinct.count(:conversation_id),
      drafts_prepared: drafts.count,
      drafts_approved: drafts.where("#{MESSAGE_ATTRS} ->> 'sent' = 'true'").count,
      emi_enquiries: tag_events(%w[emi-enquiry], range).size,
      avg_first_response_seconds: avg_first_response_seconds
    }
  end

  # AI reply drafts posted for an agent to approve (social/review/order cards).
  def ai_drafts
    account.messages.reorder(nil).where(private: true, created_at: range)
           .where("#{MESSAGE_ATTRS} ->> 'type' IN (?)", SUGGESTION_TYPES)
  end

  def channel_counts
    counts = conversations(range).joins(:inbox).group('inboxes.channel_type').count
    counts.transform_keys { |type| channel_label(type) }.sort_by { |_, n| -n }.to_h
  end

  def avg_first_response_seconds
    ReportingEvent.where(account_id: account.id, name: 'first_response', created_at: range).average(:value).to_f.round
  end

  # ── Reviews & campaigns ───────────────────────────────────────────────────

  def review_summary(type)
    list = review_entries(range).select { |r| r[:type] == type }
    rated = list.pluck(:stars).select(&:positive?)
    { count: list.size, replied: list.count { |r| r[:replied] },
      avg_stars: rated.empty? ? 0 : (rated.sum.to_f / rated.size).round(2),
      distribution: (1..5).index_with { |s| rated.count(s) } }
  end

  def tally(rows, key)
    rows.map { |row| row[key].presence || 'Other' }.tally.sort_by { |_, n| -n }.to_h
  end
end
