# Durian — ORM Daily Report (emailed to management ~9 AM IST for the previous
# day). One row per customer conversation that came in that day, in the client's
# requested layout: date, Chatwoot id, source, channel, first response, how it
# was assigned/handled, tag, auto-classification, the email it was assigned to,
# and the deal (method + id). India time; reuses the shared, range-based
# V2::Reports::OrmMonthlyQueries. No new data capture.
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
    convs = day_conversations
    assigned = assigned_emails(convs.map(&:id))
    rows = convs.map { |conv| row(conv, assigned[conv.id]) }.sort_by { |r| r[:sort_at] }
    { date_key: day_start.strftime('%Y-%m-%d'), date_label: day_start.strftime('%A, %d %B %Y'),
      generated_at: Time.current.in_time_zone(TIME_ZONE), rows: rows, totals: totals(rows) }
  end

  private

  def day_conversations
    conversations(range).joins(:inbox).where(inboxes: { channel_type: CUSTOMER_CHANNELS })
                        .where("conversations.additional_attributes ->> 'type' IS NULL " \
                               "OR conversations.additional_attributes ->> 'type' " \
                               "NOT IN ('google_review', 'website_review')")
                        .preload(:contact, :inbox).to_a
  end

  def row(conv, assigned_email)
    labels = conv.cached_label_list_array
    attrs = conv.custom_attributes || {}
    {
      sort_at: conv.created_at,
      date: local(conv.created_at),
      chatwoot_id: conv.display_id,
      source: channel_label(conv.inbox&.channel_type),
      channel: conv.inbox&.name,
      first_response: local(conv.first_reply_created_at),
      handling: labels.include?('agent-needed') ? 'Agent Needed' : 'Auto-Assigned',
      tagged: tag_label(labels),
      auto_classified: auto_classified(attrs),
      assigned_email: assigned_email,
      deal_method: deal_method(labels, attrs),
      deal_id: attrs['crm_deal_id']
    }
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

  def deal_method(labels, attrs)
    attrs['crm_deal_id'].present? || labels.include?('deal-created') ? 'Direct Deals' : 'No CRM Deal'
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
      conversations: rows.size,
      by_source: rows.map { |r| r[:source] }.tally.sort_by { |_, n| -n }.to_h,
      agent_needed: rows.count { |r| r[:handling] == 'Agent Needed' },
      deals: rows.count { |r| r[:deal_method] != 'No CRM Deal' },
      forwarded: rows.count { |r| r[:tagged].present? }
    }
  end

  def local(time)
    time&.in_time_zone(TIME_ZONE)&.strftime(STAMP)
  end
end
