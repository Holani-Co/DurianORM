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

  # When this inbound is the first reply to one of our WhatsApp campaigns, keep
  # the campaign it answers (name + the message we sent) on the conversation, so
  # the ORM agent can reply with that context and the CRM deal can name it.
  # Attribution already matched by contact + 7-day window; this only records it.
  def stamp_reply_campaign(delivery)
    conversation = @message&.conversation
    return if delivery.blank? || conversation.blank?
    return if conversation.additional_attributes['reply_campaign'].present?

    campaign = delivery.campaign
    conversation.update!(
      additional_attributes: conversation.additional_attributes.merge(
        'reply_campaign' => { 'title' => campaign.title.to_s,
                              'message' => campaign.message.to_s.truncate(500) }
      )
    )
  rescue StandardError => e
    Rails.logger.error("[whatsapp] reply-campaign stamp failed for inbox #{@inbox.id}: #{e.message}")
  end
end
