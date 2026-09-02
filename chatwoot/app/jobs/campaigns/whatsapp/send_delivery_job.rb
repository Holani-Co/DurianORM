class Campaigns::Whatsapp::SendDeliveryJob < ApplicationJob
  # Runs on :deferred (below :low, where inbound WhatsApp is handled) so a large
  # campaign fan-out never delays live customer message processing on a small box.
  queue_as :deferred

  MAX_ATTEMPTS = 3

  def perform(delivery)
    @delivery = delivery
    return unless claim_delivery!

    message_id = send_template!
    raise 'Meta did not return a message ID' if message_id.blank?

    @delivery.update!(status: 'sent', meta_message_id: message_id, sent_at: Time.current, next_retry_at: nil)
    finalize_campaign
  rescue StandardError => e
    handle_failure(e)
  end

  private

  delegate :campaign, to: :@delivery

  def claim_delivery!
    claimed = false
    @delivery.with_lock do
      @delivery.reload
      if consent_revoked?
        # Contact opted out after the audience snapshot — never send.
        @delivery.update!(status: 'cancelled', skip_reason: 'opted_out', next_retry_at: nil)
      elsif (@delivery.status_pending? || @delivery.status_queued?) && campaign.reload.execution_running?
        @delivery.update!(status: 'sending', attempt_count: @delivery.attempt_count + 1, error_code: nil, error_message: nil)
        claimed = true
      end
    end
    claimed
  end

  # Defense-in-depth against the snapshot→send window: re-verify the contact is
  # still opted in for MARKETING right before dispatch.
  def consent_revoked?
    return false unless @delivery.status_pending? || @delivery.status_queued?

    # Only cancel on an EXPLICIT opt-out recorded after the snapshot. Absence of
    # a consent record is not treated as revocation — the snapshot already
    # verified eligibility.
    consent = WhatsappConsent.current_for(inbox: campaign.inbox, contact: @delivery.contact)
    consent.present? && !consent.opted_in?
  end

  def send_template!
    processed_template_params = Whatsapp::LiquidTemplateProcessorService.new(
      campaign: campaign,
      contact: @delivery.contact
    ).process_template_params(@delivery.template_parameters)
    raise 'Template variables resolved to blank values' if processed_template_params.nil?

    name, namespace, language, parameters = Whatsapp::TemplateProcessorService.new(
      channel: campaign.inbox.channel,
      template_params: processed_template_params,
      template: campaign.whatsapp_template.processor_payload
    ).call
    raise 'Approved template could not be resolved' if name.blank?

    campaign.inbox.channel.send_template(
      @delivery.phone_number,
      { name: name, namespace: namespace, lang_code: language, parameters: parameters },
      nil
    )
  end

  def handle_failure(error)
    return unless @delivery&.persisted?

    if campaign.execution_paused?
      # Paused mid-flight — return the delivery to the queue so resume re-dispatches
      # it; don't consume its retry budget or mark it failed for a pause.
      @delivery.update!(status: 'queued', error_message: error.message, next_retry_at: nil)
    elsif @delivery.attempt_count < MAX_ATTEMPTS && campaign.execution_running?
      retry_at = (2**@delivery.attempt_count).minutes.from_now
      @delivery.update!(status: 'queued', error_message: error.message, next_retry_at: retry_at)
      self.class.set(wait_until: retry_at).perform_later(@delivery)
    else
      @delivery.update!(status: 'failed', error_message: error.message, failed_at: Time.current, next_retry_at: nil)
      finalize_campaign
    end
  end

  def finalize_campaign
    Whatsapp::CampaignFinalizeService.new(campaign).perform
  end
end
