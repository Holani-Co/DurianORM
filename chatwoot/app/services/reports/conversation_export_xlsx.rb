# Durian — Conversation export (ISS-672): every conversation of a period, in any
# inboxes (any channel), with its full history, so the team can read a chat from
# months back without scrolling. Two sheets: Conversations (one row each, with
# its ID and link) and Messages (every customer message, reply and internal note).
class Reports::ConversationExportXlsx
  TZ = 'Asia/Kolkata'.freeze
  CELL_MAX = 32_000 # Excel's cell limit is 32,767 characters
  TIME_FORMAT = '%d %b %Y %H:%M'.freeze
  CONVERSATION_HEADERS = ['Conversation ID', 'Link', 'Inbox', 'Channel', 'Status', 'Created', 'Last activity',
                          'Customer name', 'Phone', 'Email', 'Assigned agent', 'Team', 'Labels', 'Priority',
                          'CRM deal ID', 'Zoho ticket', 'Customer messages', 'Replies',
                          'First customer message'].freeze
  CONVERSATION_WIDTHS = [14, 40, 24, 12, 10, 18, 18, 24, 16, 28, 20, 20, 30, 10, 20, 14, 10, 10, 60].freeze
  MESSAGE_HEADERS = ['Conversation ID', 'Time', 'Type', 'From', 'Message', 'Attachments'].freeze
  MESSAGE_WIDTHS = [14, 18, 14, 24, 100, 12].freeze
  CHANNELS = { 'Channel::Whatsapp' => 'WhatsApp', 'Channel::Instagram' => 'Instagram',
               'Channel::FacebookPage' => 'Facebook', 'Channel::Email' => 'Email',
               'Channel::WebWidget' => 'Website chat', 'Channel::Api' => 'Reviews / API',
               'Channel::Sms' => 'SMS', 'Channel::TwilioSms' => 'SMS' }.freeze
  AI_SOURCES = %w[ai_auto_reply agent_mode].freeze

  def initialize(account:, conversations:, include_messages: true)
    @account = account
    @conversations = conversations.to_a
    @include_messages = include_messages
    @ids = @conversations.map(&:id)
  end

  def render
    package = Axlsx::Package.new
    workbook = package.workbook
    header = workbook.styles.add_style(b: true, fg_color: 'FFFFFF', bg_color: '1F3864',
                                       alignment: { wrap_text: true, vertical: :center })
    wrap = workbook.styles.add_style(alignment: { wrap_text: true, vertical: :top })
    workbook.add_worksheet(name: 'Conversations') { |sheet| fill_conversations(sheet, header) }
    workbook.add_worksheet(name: 'Messages') { |sheet| fill_messages(sheet, header, wrap) } if @include_messages
    package.to_stream.read
  end

  private

  def fill_conversations(sheet, header)
    sheet.add_row CONVERSATION_HEADERS, style: header
    counts = message_counts
    firsts = first_customer_messages
    @conversations.each do |conv|
      sheet.add_row conversation_row(conv, counts, firsts),
                    types: [:string] + Array.new(CONVERSATION_HEADERS.size - 1, nil)
    end
    sheet.column_widths(*CONVERSATION_WIDTHS)
    sheet.sheet_view.pane do |pane|
      pane.top_left_cell = 'A2'
      pane.state = :frozen_split
      pane.y_split = 1
    end
  end

  def conversation_row(conv, counts, firsts)
    attrs = conv.custom_attributes || {}
    [conv.display_id.to_s, link(conv), conv.inbox&.name, channel(conv), conv.status,
     time(conv.created_at), time(conv.last_activity_at), conv.contact&.name,
     attrs['retail_customer_phone'].presence || conv.contact&.phone_number, conv.contact&.email,
     conv.assignee&.name, conv.team&.name, conv.cached_label_list.to_s, conv.priority,
     attrs['crm_deal_id'], tickets(attrs), counts[[conv.id, 'incoming']] || 0,
     counts[[conv.id, 'outgoing']] || 0, clip(firsts[conv.id])]
  end

  def fill_messages(sheet, header, wrap)
    sheet.add_row MESSAGE_HEADERS, style: header
    display = @conversations.to_h { |c| [c.id, [c.display_id.to_s, c.contact&.name]] }
    @ids.each_slice(500) do |slice|
      Message.where(conversation_id: slice, message_type: %i[incoming outgoing])
             .reorder(:conversation_id, :id).preload(:sender, :attachments).each do |msg|
        conv_id, contact = display[msg.conversation_id]
        sheet.add_row [conv_id, time(msg.created_at), kind(msg), sender(msg, contact),
                       clip(msg.content), msg.attachments.size.positive? ? msg.attachments.size : nil],
                      style: [nil, nil, nil, nil, wrap, nil], types: [:string, nil, nil, nil, :string, nil]
      end
    end
    sheet.column_widths(*MESSAGE_WIDTHS)
  end

  def message_counts
    Message.where(conversation_id: @ids, message_type: %i[incoming outgoing], private: false)
           .reorder(nil).group(:conversation_id, :message_type).count
  end

  def first_customer_messages
    Message.where(conversation_id: @ids, message_type: :incoming)
           .select('DISTINCT ON (conversation_id) conversation_id, content')
           .reorder(:conversation_id, :id).to_h { |m| [m.conversation_id, m.content] }
  end

  def kind(msg)
    return 'Customer' if msg.incoming?

    msg.private? ? 'Internal note' : 'Reply'
  end

  def sender(msg, contact)
    return msg.sender&.name.presence || contact if msg.incoming?

    attrs = content_attributes(msg)
    return 'AI' if AI_SOURCES.include?(attrs['source'].to_s) || attrs['via'].to_s == 'agent_mode'

    msg.sender&.name.presence || 'Automation'
  end

  # Some rows store content_attributes as a JSON string.
  def content_attributes(msg)
    attrs = msg.content_attributes
    attrs = JSON.parse(attrs) if attrs.is_a?(String)
    attrs.is_a?(Hash) ? attrs : {}
  rescue JSON::ParserError
    {}
  end

  def tickets(attrs)
    list = attrs['zoho_tickets'].presence || [attrs['zoho_ticket']].compact
    list.filter_map { |t| t.is_a?(Hash) ? (t['number'] || t['id']) : nil }.join(', ').presence
  end

  def channel(conv)
    type = conv.inbox&.channel_type.to_s
    CHANNELS[type] || type.split('::').last
  end

  def link(conv)
    "#{ENV.fetch('FRONTEND_URL', '')}/app/accounts/#{@account.id}/conversations/#{conv.display_id}"
  end

  def time(value)
    value&.in_time_zone(TZ)&.strftime(TIME_FORMAT)
  end

  def clip(text)
    text.to_s.strip.presence&.slice(0, CELL_MAX)
  end
end
