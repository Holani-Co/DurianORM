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

    consent = create_consent!(status)
    # Honor the opt-out immediately: cancel any of this contact's campaign
    # deliveries that are still pending/queued for this inbox, so a message
    # already snapshotted before the STOP is never sent.
    cancel_pending_campaign_deliveries if status == 'OPTED_OUT'
    consent
  end

  private

  def create_consent!(status)
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
  rescue ActiveRecord::RecordNotUnique
    # Meta re-delivered the same inbound message; the consent is already on
    # record (unique on source_reference). Reuse it rather than raising.
    @inbox.account.whatsapp_consents.find_by(
      inbox: @inbox, source: 'inbound_message', source_reference: @message_payload[:id]
    )
  end

  def cancel_pending_campaign_deliveries
    campaign_ids = @inbox.account.campaigns.where(inbox_id: @inbox.id).select(:id)
    # rubocop:disable Rails/SkipsModelValidations
    CampaignDelivery.dispatchable
                    .where(contact_id: @contact.id, campaign_id: campaign_ids)
                    .update_all(status: 'cancelled', skip_reason: 'opted_out',
                                next_retry_at: nil, updated_at: Time.current)
    # rubocop:enable Rails/SkipsModelValidations
  end

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
