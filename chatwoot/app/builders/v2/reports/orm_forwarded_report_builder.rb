# Durian — weekly Forwarded Emails report for ONE email category: every customer
# email of that category the ORM first forwarded to a team in a Mon–Sun week
# (India time), who received it, and what came of it (deal, ticket, status).
# One row per conversation; what counts as a forward is OrmMonthlyQueries#forwards.
class V2::Reports::OrmForwardedReportBuilder
  include V2::Reports::OrmMonthlyQueries

  STAMP = '%d-%b-%Y %H:%M'.freeze
  PREVIEW_CHARS = 200

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
    sent = first_forwards
    convs = account.conversations.where(id: sent.keys).preload(:contact, :inbox, :assignee).index_by(&:id)
    cases = first_messages(sent.keys)
    rows = sent.filter_map { |id, fwd| convs[id] && row(convs[id], fwd, cases[id]) }
    { week_key: week_start.strftime('%Y-%m-%d'), week_label: week_label,
      rows: rows, totals: totals(rows, sent.values.flat_map { |fwd| fwd[:to] }) }
  end

  private

  def week_label
    "#{week_start.strftime('%-d %b')} to #{(week_start + 6.days).strftime('%-d %b %Y')}"
  end

  # Conversations forwarded in an earlier week stay out, even when something
  # goes to a team address again: a re-forward, or an agent's reply (the reply
  # box pre-fills To with the last forward's recipients).
  def first_forwards
    sent = forwards(account.messages.joins(:conversation).where(created_at: range)
                           .where("conversations.custom_attributes -> 'email_category_v2' ->> 'category' = ?", category))
    sent.except(*forwards(account.messages.where(conversation_id: sent.keys, created_at: ...week_start)).keys)
  end

  # conversation_id → the start of the customer's first email (the "case").
  def first_messages(ids)
    account.messages.where(conversation_id: ids, message_type: :incoming)
           .select('DISTINCT ON (messages.conversation_id) messages.conversation_id, LEFT(messages.content, 1000) AS content')
           .reorder('messages.conversation_id, messages.created_at')
           .to_h { |msg| [msg.conversation_id, msg.content.to_s.squish.truncate(PREVIEW_CHARS)] }
  end

  def row(conv, fwd, message)
    attrs = conv.custom_attributes || {}
    { forwarded_at: fwd[:at].in_time_zone(TIME_ZONE).strftime(STAMP), conversation_id: conv.display_id,
      subject: conv.additional_attributes&.dig('mail_subject'), message: message,
      subcategory: humanize((attrs['email_category_v2'] || {})['vertical']), forwarded_to: fwd[:to].join(', ') }
      .merge(contact_columns(conv, fwd, attrs), outcome_columns(conv, attrs))
  end

  # For a colleague-forwarded email, the customer it was forwarded for.
  def contact_columns(conv, fwd, attrs)
    contact = conv.contact
    customer = fwd[:customer] || { name: contact&.name, email: contact&.email, phone: contact&.phone_number }
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

  def totals(rows, recipients)
    { forwarded: rows.size,
      by_recipient: recipients.tally.sort_by { |email, n| [-n, email] }.to_h,
      deals: rows.count { |r| r[:deal] == 'Yes' },
      tickets: rows.count { |r| r[:ticket_no].present? },
      resolved: rows.count { |r| r[:status] == 'Resolved' } }
  end
end
