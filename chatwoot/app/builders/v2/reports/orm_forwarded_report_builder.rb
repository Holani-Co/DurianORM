# Durian — weekly Forwarded Emails report for ONE email category: every customer
# email of that category the ORM forwarded to a team in a Mon–Sun week (India
# time), who received it, and what came of it (deal, ticket, status).
#
# A forward is an outgoing email on the conversation addressed to anyone but the
# customer: the bridge's auto / agent-confirmed forward, or the ⋮ Forward button.
# A plain reply carries no `to_emails` (or only the customer's), so it never
# counts. One row per conversation, dated by its first forward of the week.
class V2::Reports::OrmForwardedReportBuilder
  include V2::Reports::OrmMonthlyQueries

  STAMP = '%d-%b-%Y %H:%M'.freeze
  PREVIEW_CHARS = 200
  # The bridge's private note on a customer email a colleague forwarded in (the
  # contact is then the colleague): "… forwarded this on behalf of **Name**
  # (email · phone)." The acknowledgement goes to that customer, not a team.
  ON_BEHALF = /forwarded this on behalf of \*\*(?<name>.+?)\*\* \((?<email>[^\s·)]+)(?: · (?<phone>[^)]+))?\)/

  attr_reader :account, :category, :week_start, :range

  # week: 'YYYY-MM-DD', any day of the week (defaults to last week, India time).
  def initialize(account:, category:, week: nil)
    @account = account
    @category = category.to_s
    zone = ActiveSupport::TimeZone[TIME_ZONE]
    @week_start = (week.present? ? zone.parse(week) : zone.now - 1.week).beginning_of_week(:monday)
    @range = @week_start...(@week_start + 1.week)
  end

  def build
    sent = forwards
    convs = account.conversations.where(id: sent.keys).preload(:contact, :assignee).index_by(&:id)
    cases = first_messages(sent.keys)
    rows = sent.filter_map { |id, fwd| convs[id] && row(convs[id], fwd, cases[id]) }
    envelope(rows, convs.values.first)
  end

  private

  def envelope(rows, any_conv)
    { category: category, category_name: category_name(any_conv&.custom_attributes&.dig('email_category_v2') || { 'category' => category }),
      week_key: week_start.strftime('%Y-%m-%d'),
      week_label: "#{week_start.strftime('%-d %b')} to #{(week_start + 6.days).strftime('%-d %b %Y')}",
      rows: rows, totals: totals(rows) }
  end

  # conversation_id → { at: its first forward this week, to: [recipients] },
  # in forward order. The customer's own address never counts as a recipient.
  def forwards
    messages = forward_candidates.to_a
    ids = messages.map(&:conversation_id).uniq
    @on_behalf = on_behalf_customers(ids)
    contact_email = Conversation.where(id: ids).joins(:contact).pluck(:id, 'contacts.email').to_h
    messages.each_with_object({}) do |msg, out|
      customer = [contact_email[msg.conversation_id], @on_behalf.dig(msg.conversation_id, :email)]
      to = recipients(msg) - customer.compact.map(&:downcase)
      (out[msg.conversation_id] ||= { at: msg.created_at, to: [] })[:to] |= to if to.any?
    end
  end

  # conversation_id → { name:, email:, phone: } of the real customer on a
  # colleague-forwarded email.
  def on_behalf_customers(ids)
    account.messages.where(conversation_id: ids, private: true)
           .where('messages.content LIKE ?', '%forwarded this on behalf of%')
           .pluck(:conversation_id, :content)
           .each_with_object({}) do |(conv_id, content), out|
             found = content.match(ON_BEHALF)
             out[conv_id] ||= { name: found[:name], email: found[:email], phone: found[:phone] } if found
           end
  end

  def recipients(msg)
    Array(msg.content_attributes['to_emails']).map { |email| email.to_s.strip.downcase }.compact_blank
  end

  # This category's outgoing emails in the week that name a To address.
  def forward_candidates
    account.messages.joins(:conversation)
           .where(message_type: :outgoing, private: false, created_at: range)
           .where("conversations.custom_attributes -> 'email_category_v2' ->> 'category' = ?", category)
           .where("#{MESSAGE_ATTRS} -> 'to_emails' IS NOT NULL")
           .reorder(:created_at)
  end

  # conversation_id → the start of the customer's first email (the "case").
  def first_messages(ids)
    account.messages.where(conversation_id: ids, message_type: :incoming)
           .select('DISTINCT ON (messages.conversation_id) messages.conversation_id, messages.content')
           .reorder('messages.conversation_id, messages.created_at')
           .to_h { |msg| [msg.conversation_id, msg.content.to_s.squish.truncate(PREVIEW_CHARS)] }
  end

  def row(conv, fwd, message)
    attrs = conv.custom_attributes || {}
    { forwarded_at: fwd[:at].in_time_zone(TIME_ZONE).strftime(STAMP), conversation_id: conv.display_id,
      subject: conv.additional_attributes&.dig('mail_subject'), message: message,
      subcategory: humanize((attrs['email_category_v2'] || {})['vertical']),
      forwarded_to: fwd[:to].join(', '), recipients: fwd[:to] }
      .merge(contact_columns(conv, attrs), outcome_columns(conv, attrs))
  end

  # For a colleague-forwarded email, the customer it was forwarded for.
  def contact_columns(conv, attrs)
    contact = conv.contact
    customer = @on_behalf[conv.id] || { name: contact&.name, email: contact&.email, phone: contact&.phone_number }
    { customer: customer[:name], email: customer[:email], mobile: attrs['retail_customer_phone'].presence || customer[:phone] }
  end

  # Who forwarded it and what came of it.
  def outcome_columns(conv, attrs)
    labels = conv.cached_label_list_array
    ticket = tickets_of(conv).first || {}
    has_deal = attrs['crm_deal_id'].present? || labels.include?('deal-created')
    { forwarded_by: labels.include?('auto-forwarded') ? 'AI (auto)' : 'Agent',
      deal: has_deal ? 'Yes' : 'No', deal_no: attrs['crm_deal_no'].presence || attrs['crm_deal_id'],
      deal_stage: attrs['crm_deal_stage'], ticket_no: ticket[:number], ticket_status: ticket[:status],
      status: humanize(conv.status), agent: conv.assignee&.name }
  end

  def totals(rows)
    { forwarded: rows.size,
      by_recipient: rows.flat_map { |r| r[:recipients] }.tally.sort_by { |email, n| [-n, email] }.to_h,
      deals: rows.count { |r| r[:deal] == 'Yes' },
      tickets: rows.count { |r| r[:ticket_no].present? },
      resolved: rows.count { |r| r[:status] == 'Resolved' } }
  end
end
