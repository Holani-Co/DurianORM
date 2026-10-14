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
  rescue StandardError => e
    Rails.logger.error("[whatsapp] reply-campaign stamp failed for inbox #{@inbox.id}: #{e.message}")
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
