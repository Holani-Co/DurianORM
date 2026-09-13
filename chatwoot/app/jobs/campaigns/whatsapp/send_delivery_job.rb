class Campaigns::Whatsapp::SendDeliveryJob < ApplicationJob
  # Runs on :deferred (below :low, where inbound WhatsApp is handled) so a large
  # campaign fan-out never delays live customer message processing on a small box.
  queue_as :deferred

  MAX_ATTEMPTS = 3
  # Meta error codes that signal throughput / messaging-tier throttling rather than
  # a permanent per-message failure. These are backed off over a long window (and
  # trip the fan-out cooldown) instead of burning the fast-retry budget.
  # 130429 throughput rate limit, 131048 spam rate limit, 131049 per-user marketing
  # frequency cap, 80007 business-account rate limit, 133016 too many requests.
  RATE_LIMIT_ERROR_CODES = %w[130429 131048 131049 80007 133016].freeze
  RATE_LIMIT_MAX_ATTEMPTS = 6
  RATE_LIMIT_BACKOFF = ENV.fetch('WHATSAPP_CAMPAIGN_RATE_LIMIT_BACKOFF_SECONDS', 900).to_i.seconds
  COOLDOWN_TTL = ENV.fetch('WHATSAPP_CAMPAIGN_COOLDOWN_SECONDS', 60).to_i.seconds

  def perform(delivery)
    @delivery = delivery
    return unless claim_delivery!

    result = send_template!
    if result[:message_id].present?
      @delivery.update!(status: 'sent', meta_message_id: result[:message_id], sent_at: Time.current, next_retry_at: nil)
      finalize_campaign
    else
      handle_send_error(result)
    end
  rescue StandardError => e
    handle_failure(error_message: e.message)
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
    ).process_template_params(campaign.whatsapp_media_params(@delivery.template_parameters))
    raise 'Template variables resolved to blank values' if processed_template_params.nil?

    name, namespace, language, parameters = Whatsapp::TemplateProcessorService.new(
      channel: campaign.inbox.channel,
      template_params: processed_template_params,
      template: campaign.whatsapp_template.processor_payload
    ).call
    raise 'Approved template could not be resolved' if name.blank?

    campaign.inbox.channel.send_campaign_template(
      @delivery.phone_number,
      { name: name, namespace: namespace, lang_code: language, parameters: parameters }
    )
  end

  # A Meta send failed. Rate-limit / tier responses are backed off gently (and trip
  # the fan-out cooldown) so a daily cap doesn't instantly fail the whole audience;
  # everything else goes through the normal fast-retry budget. Either way the real
  # Meta error code/message is persisted on the delivery for reporting.
  def handle_send_error(result)
    if RATE_LIMIT_ERROR_CODES.include?(result[:error_code].to_s)
      handle_rate_limit(result)
    else
      handle_failure(error_code: result[:error_code], error_message: result[:error_message])
    end
  end

  def handle_rate_limit(result)
    Campaigns::Whatsapp::RateLimitCooldown.trip!(campaign.inbox_id, COOLDOWN_TTL)

    if @delivery.attempt_count < RATE_LIMIT_MAX_ATTEMPTS && campaign.execution_running?
      retry_at = RATE_LIMIT_BACKOFF.from_now
      @delivery.update!(status: 'queued', error_code: result[:error_code], error_message: result[:error_message], next_retry_at: retry_at)
      self.class.set(wait_until: retry_at).perform_later(@delivery)
    else
      fail_delivery(error_code: result[:error_code], error_message: result[:error_message])
    end
  end

  def handle_failure(error_code: nil, error_message: nil)
    return unless @delivery&.persisted?

    if campaign.execution_paused?
      # Paused mid-flight — return the delivery to the queue so resume re-dispatches
      # it; don't consume its retry budget or mark it failed for a pause.
      @delivery.update!(status: 'queued', error_code: error_code, error_message: error_message, next_retry_at: nil)
    elsif @delivery.attempt_count < MAX_ATTEMPTS && campaign.execution_running?
      retry_at = (2**@delivery.attempt_count).minutes.from_now
      @delivery.update!(status: 'queued', error_code: error_code, error_message: error_message, next_retry_at: retry_at)
      self.class.set(wait_until: retry_at).perform_later(@delivery)
    else
      fail_delivery(error_code: error_code, error_message: error_message)
    end
  end

  def fail_delivery(error_code: nil, error_message: nil)
    @delivery.update!(status: 'failed', error_code: error_code, error_message: error_message, failed_at: Time.current, next_retry_at: nil)
    finalize_campaign
  end

  def finalize_campaign
    Whatsapp::CampaignFinalizeService.new(campaign).perform
  end
end
