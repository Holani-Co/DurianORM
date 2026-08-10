# Durian — downloadable CSV exports for the ORM reports section.
#
# Each action streams a CSV for the selected date range. Deals use the client's
# Zoho CRM "Deals" import column layout so the file drops straight into Zoho;
# tickets / EMI / overview use their own sensible columns. Reviews are excluded
# — they already have their own report (reviews_reports_controller).
class Api::V1::Accounts::OrmExportsController < Api::V1::Accounts::BaseController
  before_action :check_authorization

  # Zoho CRM Deals import layout (order matters — mirrors the client's template).
  DEAL_HEADERS = ['Created Time', 'Deal Owner', 'Deal Name', 'Deal No', 'Business Verticals',
                  'Product Category', 'Enquiry Source', 'Sub Source', 'Integration Source',
                  'City', 'Email', 'Mobile', 'Sales Person Name (Sales Person)', 'Stage',
                  'Remarks', 'Zip Code New', 'Description'].freeze
  # Per-deal vertical label the bridge tags → Zoho "Business Verticals" value.
  VERTICAL_LABELS = { 'deal-doors' => 'Doors', 'deal-fhc' => 'Full Home',
                      'deal-bulk' => 'Project', 'deal-product' => 'Furniture',
                      'deal-franchise' => 'Dealership / Franchise' }.freeze
  CHANNEL_SOURCE = { 'Channel::Instagram' => 'Instagram', 'Channel::FacebookPage' => 'Facebook',
                     'Channel::Email' => 'Email', 'Channel::Api' => 'API',
                     'Channel::Whatsapp' => 'WhatsApp', 'Channel::WebWidget' => 'Live chat' }.freeze
  DEAL_STAGE = 'Qualified Deal (Please Select This )'.freeze

  def deals
    rows = tagged_conversations('deal-created').map { |c| deal_row(c) }
    send_csv('orm-deals', DEAL_HEADERS, rows)
  end

  def tickets
    headers = ['Created Time', 'Ticket No', 'Subject', 'Status', 'Source',
               'Contact Name', 'Email', 'Mobile', 'Channel']
    rows = tagged_conversations('zoho-ticket').flat_map { |c| ticket_rows(c) }
    send_csv('orm-tickets', headers, rows)
  end

  def emi
    headers = ['Created Time', 'Contact Name', 'Email', 'Mobile', 'City', 'Channel']
    rows = tagged_conversations('emi-enquiry').map { |c| emi_row(c) }
    send_csv('orm-emi-enquiries', headers, rows)
  end

  def overview
    data = V2::Reports::OrmOverviewBuilder.new(account: Current.account, params: report_params).build
    rows = [
      ['Conversations', data[:conversations][:total]],
      ['AI auto-replies sent', data[:ai][:auto_replies_sent]],
      ['AI auto-handled conversations', data[:ai][:auto_handled_conversations]],
      ['Awaiting a human (open)', data[:ai][:agent_needed_open]],
      ['Avg first response (seconds)', data[:first_response][:avg_seconds]],
      ['Deals created', data[:deals][:created]],
      ['Tickets raised', data[:tickets][:raised]],
      ['EMI enquiries', data[:emi][:enquiries]],
      ['Reviews received', data[:reviews][:count]],
      ['Avg review rating', data[:reviews][:avg_stars]]
    ]
    data[:conversations][:by_channel].each { |ch, n| rows << ["Conversations · #{ch}", n] }
    data[:categories].each { |cat, n| rows << ["Enquiry · #{cat}", n] }
    send_csv('orm-overview', %w[Metric Value], rows)
  end

  private

  def range
    since = Time.zone.at((params[:since].presence || 30.days.ago.to_i).to_i)
    till  = Time.zone.at((params[:until].presence || Time.current.to_i).to_i)
    since..till
  end

  def report_params
    { since: params[:since], until: params[:until] }
  end

  # Conversations started in range that carry `label`, with contact + inbox
  # preloaded (separate queries, so the label join can't clash with eager load).
  def tagged_conversations(label)
    Current.account.conversations
           .where(created_at: range)
           .joins("INNER JOIN taggings ON taggings.taggable_id = conversations.id " \
                  "AND taggings.taggable_type = 'Conversation' AND taggings.context = 'labels'")
           .joins('INNER JOIN tags ON tags.id = taggings.tag_id')
           .where(tags: { name: label })
           .distinct
           .preload(:contact, :inbox)
  end

  def deal_row(conv)
    ca = conv.custom_attributes || {}
    owner = ca['retail_deal_owner'] || {}
    vertical = VERTICAL_LABELS.values_at(*(conv.cached_label_list_array & VERTICAL_LABELS.keys)).first
    [
      conv.created_at.strftime('%Y-%m-%d %H:%M:%S'),
      owner['owner_name'], conv.contact&.name, ca['crm_deal_id'],
      vertical || ca['phase2_category'], '',
      channel_source(conv), '', conv.inbox&.name,
      owner['city'], conv.contact&.email, mobile(conv, ca),
      owner['owner_name'], DEAL_STAGE, '', '', ''
    ]
  end

  def ticket_rows(conv)
    ca = conv.custom_attributes || {}
    tickets = ca['zoho_tickets'].presence || [ca['zoho_ticket']].compact
    tickets.map do |t|
      [t['created_at'] || conv.created_at.strftime('%Y-%m-%d %H:%M:%S'),
       t['number'] || t['id'], t['subject'], t['status'], t['source'],
       conv.contact&.name, conv.contact&.email, mobile(conv, ca), channel_source(conv)]
    end
  end

  def emi_row(conv)
    ca = conv.custom_attributes || {}
    [conv.created_at.strftime('%Y-%m-%d %H:%M:%S'), conv.contact&.name,
     conv.contact&.email, mobile(conv, ca), (ca['retail_deal_owner'] || {})['city'],
     channel_source(conv)]
  end

  def channel_source(conv)
    ct = conv.inbox&.channel_type
    CHANNEL_SOURCE[ct] || ct.to_s.split('::').last
  end

  def mobile(conv, custom_attributes)
    custom_attributes['retail_customer_phone'].presence || conv.contact&.phone_number
  end

  def send_csv(name, headers, rows)
    csv = CSV.generate do |c|
      c << headers
      rows.each { |row| c << row }
    end
    send_data csv, filename: "#{name}-#{Date.current}.csv", type: 'text/csv'
  end

  def check_authorization
    authorize :report, :view?
  end
end
