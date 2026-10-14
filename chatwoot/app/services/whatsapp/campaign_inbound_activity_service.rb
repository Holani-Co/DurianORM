class Whatsapp::CampaignInboundActivityService
  def self.perform(inbox, contact, message, message_payload)
    new(inbox: inbox, contact: contact, message: message, message_payload: message_payload).perform
  end

  def initialize(inbox:, contact:, message:, message_payload:)
    @inbox = inbox
    @contact = contact
    @message = message
    @message_payload = message_payload
  end

  def perform
    Whatsapp::ConsentCaptureService.new(inbox: @inbox, contact: @contact, message_payload: @message_payload).perform
    delivery = Whatsapp::CampaignReplyAttributionService.new(
      inbox: @inbox,
      contact: @contact,
      received_at: @message.created_at
    ).perform
    stamp_reply_campaign(delivery)
  end

  private

  MEDIA_HEADER_FORMATS = %w[image video document].freeze

  # When this inbound is the first reply to one of our WhatsApp campaigns, keep
  # the campaign it answers (name, the message we sent, and the header media type
  # if any) on the conversation, so the ORM agent can reply with that context,
  # the CRM deal can name it, and the agent-only note can show what we sent.
  # Attribution already matched by contact + 7-day window; this only records it.
  def stamp_reply_campaign(delivery)
    conversation = @message&.conversation
    return if delivery.blank? || conversation.blank?
    return if conversation.additional_attributes['reply_campaign'].present?

    campaign = delivery.campaign
    reply_campaign = { 'title' => campaign.title.to_s,
                       'message' => campaign.message.to_s.truncate(500),
                       'media_type' => reply_campaign_media_type(campaign) }.compact
    conversation.update!(
      additional_attributes: conversation.additional_attributes.merge('reply_campaign' => reply_campaign)
    )
    post_reply_campaign_note(conversation, reply_campaign)
  rescue StandardError => e
    Rails.logger.error("[whatsapp] reply-campaign stamp failed for inbox #{@inbox.id}: #{e.message}")
  end

  # Agent-only note so a human replying to a bare "Hi! Can I get more
  # information on this?" sees it is a reply to one of our campaigns and what we
  # sent. Posted here (not from the bridge) because the reply_campaign stamp
  # lands after the webhook payload is built — the campaign reply is usually a
  # single message, so a bridge-side snapshot read would miss it. Best-effort.
  def post_reply_campaign_note(conversation, reply_campaign)
    return if ENV.fetch('WHATSAPP_CAMPAIGN_NOTE_ENABLED', 'true').to_s.downcase == 'false'

    lines = ['📣 Reply to our WhatsApp campaign — context for the agent',
             "• Campaign: #{reply_campaign['title'].presence || 'a Durian campaign'}"]
    lines << "• We sent: #{reply_campaign['message']}" if reply_campaign['message'].present?
    lines << "• Media sent: #{reply_campaign['media_type']}" if reply_campaign['media_type'].present?
    Messages::MessageBuilder.new(nil, conversation, { content: lines.join("\n"), private: true }).perform
  rescue StandardError => e
    Rails.logger.error("[whatsapp] reply-campaign note failed for inbox #{@inbox.id}: #{e.message}")
  end

  # 'image' / 'video' / 'document' when the campaign's template has a media
  # header, else nil (plain-text campaign or a TEXT header).
  def reply_campaign_media_type(campaign)
    components = Array(campaign.whatsapp_template&.components)
    header = components.find { |c| c.is_a?(Hash) && c['type'].to_s.casecmp?('HEADER') }
    format = header && header['format'].to_s.downcase
    format if MEDIA_HEADER_FORMATS.include?(format)
  end
end
