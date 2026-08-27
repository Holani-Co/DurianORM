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
    @campaign.completed!
  end
end
