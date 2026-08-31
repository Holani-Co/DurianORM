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
    Whatsapp::CampaignReplyAttributionService.new(
      inbox: @inbox,
      contact: @contact,
      received_at: @message.created_at
    ).perform
  end
end
