# Durian — ORM Daily Report (emailed to management ~9 AM IST for the previous
# day). One row per customer conversation, in the client's requested layout plus
# the extra columns they asked for (customer, category, showroom, agent, ticket,
# deal details, ...). Rows are the conversations that ARRIVED that day UNION the
# conversations where a DEAL was booked that day (the latter may have started
# earlier — those are the deals the arrival-only view was missing). Deals are
# counted by the `deal-created` label event, dated by when it was applied, so the
# count matches the monthly report. India time; reuses V2::Reports::OrmMonthlyQueries.
class V2::Reports::OrmDailyReportBuilder
  include V2::Reports::OrmMonthlyQueries

  TIME_ZONE = 'Asia/Kolkata'.freeze
  # Customer channels that belong in the log (reviews live in their own inbox and
  # don't fit these columns, so they're excluded).
  CUSTOMER_CHANNELS = ['Channel::Email', 'Channel::Whatsapp', 'Channel::Instagram', 'Channel::FacebookPage'].freeze
  STAMP = '%d-%b-%Y %H:%M'.freeze

  attr_reader :account, :day_start, :range

  # date: 'YYYY-MM-DD' (defaults to yesterday, India time).
  def initialize(account:, date: nil)
    @account = account
    zone = ActiveSupport::TimeZone[TIME_ZONE]
    day = date ? zone.parse(date) : zone.now.yesterday
    @day_start = day.beginning_of_day
    @range = @day_start...(@day_start + 1.day)
  end

  def build
    @deal_at = deal_events_by_conv(range)
    convs = row_conversations
    authors = deal_authors(range)
    assigned = assigned_emails(convs.map(&:id))
    rows = convs.map { |conv| row(conv, assigned[conv.id], authors) }.sort_by { |r| r[:sort_at] }
    envelope(rows)
  end

  private

  def envelope(rows)
    { date_key: day_start.strftime('%Y-%m-%d'), date_label: day_start.strftime('%A, %d %B %Y'),
      generated_at: Time.current.in_time_zone(TIME_ZONE), rows: rows, totals: totals(rows) }
  end

  # Conversations that ARRIVED today, plus those where a deal was booked today
  # (which may have started earlier — the deals the arrival-only view missed).
  def row_conversations
    arrived = day_conversations
    @arrived_ids = arrived.to_set(&:id)
    arrived + conversations_by_id(@deal_at.keys - @arrived_ids.to_a).values
  end

  # conversation_id → the moment its deal was booked (label applied) in the day.
  def deal_events_by_conv(on_range)
    tag_events(['deal-created'], on_range).each_with_object({}) do |event, out|
      out[event[:conv_id]] ||= event[:at]
    end
  end

  def day_conversations
    conversations(range).joins(:inbox).where(inboxes: { channel_type: CUSTOMER_CHANNELS })
                        .where("conversations.additional_attributes ->> 'type' IS NULL " \
                               "OR conversations.additional_attributes ->> 'type' " \
                               "NOT IN ('google_review', 'website_review')")
                        .preload(:contact, :inbox, :assignee, :team).to_a
  end

  def row(conv, assigned_email, authors)
    attrs = conv.custom_attributes || {}
    labels = conv.cached_label_list_array
    template_columns(conv, attrs, labels, assigned_email)
      .merge(deal_columns(conv, attrs, authors))
      .merge(contact_columns(conv, attrs))
      .merge(case_columns(conv, attrs))
  end

  # The client's original template columns.
  def template_columns(conv, attrs, labels, assigned_email)
    at = primary_at(conv)
    { sort_at: at, date: local(at), chatwoot_id: conv.display_id,
      source: channel_label(conv.inbox&.channel_type), channel: conv.inbox&.name,
      first_response: local(conv.first_reply_created_at),
      handling: labels.include?('agent-needed') ? 'Agent Needed' : 'Auto-Assigned',
      tagged: tag_label(labels), auto_classified: auto_classified(attrs), assigned_email: assigned_email }
  end

  def deal_columns(conv, attrs, authors)
    has_deal = @deal_at.key?(conv.id) || attrs['crm_deal_id'].present?
    { deal_method: has_deal ? 'Direct Deals' : 'No CRM Deal',
      deal_id: attrs['crm_deal_no'].presence || attrs['crm_deal_id'],
      deal_stage: attrs['crm_deal_stage'],
      deal_created_by: authors[conv.id], deal_url: attrs['crm_deal_url'] }
  end

  def contact_columns(conv, attrs)
    contact = conv.contact
    { customer: contact&.name,
      mobile: attrs['retail_customer_phone'].presence || contact&.phone_number,
      email: contact&.email, city: contact&.additional_attributes&.dig('city'),
      subject: conv.additional_attributes&.dig('mail_subject') }
  end

  def case_columns(conv, attrs)
    category = attrs['email_category_v2'] || {}
    ticket = tickets_of(conv).first || {}
    { category: category_name(category), subcategory: humanize(category['vertical']),
      product_line: product_line(conv), showroom: showroom_of(attrs),
      status: humanize(conv.status), agent: conv.assignee&.name, team: conv.team&.name,
      priority: humanize(conv.priority), ticket_no: ticket[:number], ticket_status: ticket[:status] }
  end

  # Rows for deals booked on older conversations are dated by the deal moment;
  # rows for conversations that arrived today are dated by arrival.
  def primary_at(conv)
    return conv.created_at if range.cover?(conv.created_at)

    @deal_at[conv.id] || conv.created_at
  end

  def tag_label(labels)
    return 'Manually - sent' if labels.include?('manually-sent')
    return 'Auto - forwarded' if labels.include?('auto-forwarded')

    ''
  end

  # What the classifier / router decided: the retail showroom if one was tagged,
  # else the email category (the client-editable display name).
  def auto_classified(attrs)
    owner = attrs['retail_deal_owner'] || {}
    return "Showroom - #{owner['city'].presence || owner['location']}" if owner['owner_id'].present?

    email_category(attrs['email_category_v2'] || {}).presence || humanize(attrs['phase2_category']).to_s
  end

  def email_category(category)
    return '' if category['category'].blank? || category['category'] == 'fallback'

    category_name(category)
  end

  def showroom_of(attrs)
    owner = attrs['retail_deal_owner'] || {}
    owner['city'].presence || owner['location']
  end

  # conversation_id → the address(es) we forwarded/assigned it to: the to_emails
  # of our outgoing emails, minus the customer's own address.
  def assigned_emails(ids)
    return {} if ids.empty?

    customer = Conversation.where(id: ids).joins(:contact).pluck(:id, 'contacts.email').to_h
    out = Hash.new { |hash, key| hash[key] = [] }
    forwarded_messages(ids).find_each do |msg|
      to = Array(msg.content_attributes['to_emails']).reject do |email|
        email.blank? || email.casecmp?(customer[msg.conversation_id].to_s)
      end
      out[msg.conversation_id].concat(to)
    end
    out.transform_values { |emails| emails.uniq.join(', ') }
  end

  def forwarded_messages(ids)
    account.messages.where(conversation_id: ids, message_type: :outgoing, private: false)
           .where("#{MESSAGE_ATTRS} -> 'to_emails' IS NOT NULL")
  end

  def totals(rows)
    {
      conversations: @arrived_ids.size,
      by_source: rows.map { |r| r[:source] }.tally.sort_by { |_, n| -n }.to_h,
      agent_needed: rows.count { |r| r[:handling] == 'Agent Needed' },
      deals: @deal_at.size,
      forwarded: rows.count { |r| r[:tagged].present? }
    }
  end

  def local(time)
    time&.in_time_zone(TIME_ZONE)&.strftime(STAMP)
  end
end
