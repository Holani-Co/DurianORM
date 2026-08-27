class Whatsapp::CampaignControlService
  def initialize(campaign)
    @campaign = campaign
  end

  def pause!
    @campaign.transition_execution_to!(:paused)
  end

  def resume!
    if @campaign.audience_snapshot_at.blank?
      @campaign.transition_execution_to!(:scheduled)
      return
    end

    prepare_failed_deliveries if @campaign.execution_failed?
    @campaign.transition_execution_to!(:queued)
    @campaign.transition_execution_to!(:running)
    enqueue_dispatchable_deliveries
    Whatsapp::CampaignFinalizeService.new(@campaign).perform
  end

  def cancel!
    @campaign.transition_execution_to!(:cancelled)
    cancel_dispatchable_deliveries
    @campaign.refresh_delivery_counts!
    @campaign.completed!
  end

  private

  def prepare_failed_deliveries
    # rubocop:disable Rails/SkipsModelValidations
    @campaign.campaign_deliveries.where(status: 'failed').update_all(
      status: 'queued', failed_at: nil, next_retry_at: nil, error_code: nil, error_message: nil, updated_at: Time.current
    )
    # rubocop:enable Rails/SkipsModelValidations
  end

  def enqueue_dispatchable_deliveries
    Campaigns::Whatsapp::DispatchBatchJob.perform_later(@campaign)
  end

  def cancel_dispatchable_deliveries
    # rubocop:disable Rails/SkipsModelValidations
    @campaign.campaign_deliveries.dispatchable.update_all(status: 'cancelled', next_retry_at: nil, updated_at: Time.current)
    # rubocop:enable Rails/SkipsModelValidations
  end
end
