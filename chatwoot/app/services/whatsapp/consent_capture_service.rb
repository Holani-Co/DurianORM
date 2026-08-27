class Whatsapp::ConsentCaptureService
  OPT_OUT_KEYWORDS = ['STOP', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT', 'STOP PROMOTIONS'].freeze
  OPT_IN_KEYWORDS = %w[START SUBSCRIBE].freeze

  def initialize(inbox:, contact:, message_payload:)
    @inbox = inbox
    @contact = contact
    @message_payload = message_payload
  end

  def perform
    status = consent_status
    return if status.blank?

    @inbox.account.whatsapp_consents.create!(
      inbox: @inbox,
      contact: @contact,
      purpose: 'MARKETING',
      status: status,
      source: 'inbound_message',
      source_reference: @message_payload[:id],
      recorded_at: Time.current,
      details: { content: normalized_content }
    )
  end

  private

  def consent_status
    return 'OPTED_OUT' if opt_out_content?
    return 'OPTED_IN' if OPT_IN_KEYWORDS.include?(normalized_content)

    nil
  end

  def opt_out_content?
    OPT_OUT_KEYWORDS.include?(normalized_content) ||
      normalized_content.start_with?('STOP ', 'UNSUBSCRIBE ')
  end

  def normalized_content
    @normalized_content ||= raw_content.to_s.strip.upcase
  end

  def raw_content
    @message_payload.dig(:interactive, :button_reply, :id) ||
      @message_payload.dig(:interactive, :button_reply, :title) ||
      @message_payload.dig(:button, :text) ||
      @message_payload.dig(:text, :body)
  end
end
