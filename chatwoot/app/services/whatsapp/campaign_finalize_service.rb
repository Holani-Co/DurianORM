class Whatsapp::CampaignFinalizeService
  ACTIVE_STATUSES = %w[pending queued sending].freeze

  def initialize(campaign)
    @campaign = campaign
  end

  def perform
    @campaign.refresh_delivery_counts!
    return if @campaign.campaign_deliveries.exists?(status: ACTIVE_STATUSES)
    return unless @campaign.execution_running?

    @campaign.transition_execution_to!(:completed)
    @campaign.completed! unless @campaign.completed?

    # Sends reference the Meta media id from here on and finalize only runs when no
    # active deliveries remain, so the local blob is no longer needed.
    @campaign.media.purge_later if @campaign.media.attached? && @campaign.whatsapp_media_id.present?
  end
end
