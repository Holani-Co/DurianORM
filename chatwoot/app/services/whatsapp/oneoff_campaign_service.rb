class Whatsapp::OneoffCampaignService
  pattr_initialize [:campaign!]

  def perform
    validate_campaign!
    return unless claim_campaign!

    Whatsapp::CampaignAudienceSnapshotService.new(campaign).perform
    ensure_whatsapp_media_uploaded!
    campaign.transition_execution_to!(:running)
    Campaigns::Whatsapp::DispatchBatchJob.perform_later(campaign)
    Whatsapp::CampaignFinalizeService.new(campaign).perform
  rescue StandardError => e
    # Mark the campaign failed so TriggerScheduledItemsJob stops re-picking it
    # (it filters execution_status: :scheduled) — otherwise a validation failure
    # here loops every 5 minutes forever. Then re-raise so the trigger job
    # surfaces the error normally (Sidekiq's exponential backoff makes a
    # permanently-invalid campaign's retries negligible).
    fail_campaign!(e.message)
    raise
  end

  private

  # Upload the campaign media to Meta once at launch and persist the returned id so
  # every per-delivery send references it by id (avoids re-uploading and the 132012
  # rejection from stale/blocked public URLs). A failure raises → the rescue above
  # marks the campaign failed with a visible error.
  def ensure_whatsapp_media_uploaded!
    return unless campaign.media.attached?
    return if campaign.whatsapp_media_id.present?

    media_id = Whatsapp::CampaignMediaService.new(blob: campaign.media.blob, template: campaign.whatsapp_template).upload_to!(channel)
    campaign.update!(whatsapp_media_id: media_id)
  end

  def fail_campaign!(message)
    return if campaign.execution_terminal?

    campaign.transition_execution_to!(:failed, error: message)
  rescue StandardError => e
    Rails.logger.error("[whatsapp campaign #{campaign.id}] could not mark failed: #{e.message}")
  end

  delegate :inbox, to: :campaign
  delegate :channel, to: :inbox

  def claim_campaign!
    claimed = false
    campaign.with_lock do
      if campaign.execution_scheduled?
        campaign.update!(execution_status: :queued)
        claimed = true
      end
    end
    claimed
  end

  def validate_campaign_type!
    raise "Invalid campaign #{campaign.id}" unless whatsapp_campaign? && campaign.one_off?
  end

  def whatsapp_campaign?
    campaign.inbox.inbox_type == 'Whatsapp'
  end

  def validate_campaign_status!
    raise 'Completed Campaign' if campaign.completed?
  end

  def validate_provider!
    raise 'WhatsApp Cloud provider required' if channel.provider != 'whatsapp_cloud'
  end

  def validate_feature_flag!
    raise 'WhatsApp campaigns feature not enabled' unless campaign.account.feature_enabled?(:whatsapp_campaign)
  end

  def validate_campaign!
    validate_campaign_type!
    validate_campaign_status!
    validate_provider!
    validate_feature_flag!
    validate_template!
  end

  def validate_template!
    raise 'Approved WhatsApp template required' unless campaign.whatsapp_template&.approved?
    raise 'Template parameters required' if campaign.template_params.blank?
  end
end
