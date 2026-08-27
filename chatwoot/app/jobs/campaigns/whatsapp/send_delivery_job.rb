class Campaigns::Whatsapp::SendDeliveryJob < ApplicationJob
  queue_as :low

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
      if (@delivery.status_pending? || @delivery.status_queued?) && campaign.reload.execution_running?
        @delivery.update!(status: 'sending', attempt_count: @delivery.attempt_count + 1, error_code: nil, error_message: nil)
        claimed = true
      end
    end
    claimed
  end

  def send_template!
    processed_template_params = Whatsapp::LiquidTemplateProcessorService.new(
      campaign: campaign,
      contact: @delivery.contact
    ).process_template_params(@delivery.template_parameters)
    raise 'Template variables resolved to blank values' if processed_template_params.nil?

    name, namespace, language, parameters = Whatsapp::TemplateProcessorService.new(
      channel: campaign.inbox.channel,
      template_params: processed_template_params
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

    if @delivery.attempt_count < MAX_ATTEMPTS && campaign.execution_running?
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
