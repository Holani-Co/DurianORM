class Campaigns::Whatsapp::ReconcileJob < ApplicationJob
  queue_as :scheduled_jobs

  STALE_SENDING_AGE = 30.minutes

  def perform
    Campaign.execution_running.find_each do |campaign|
      reconcile_stale_deliveries(campaign)
      enqueue_dispatch(campaign)
      Whatsapp::CampaignFinalizeService.new(campaign).perform
    end
  end

  private

  def reconcile_stale_deliveries(campaign)
    campaign.campaign_deliveries.status_sending.where(updated_at: ...STALE_SENDING_AGE.ago).find_each do |delivery|
      delivery.update!(
        status: 'failed',
        failed_at: Time.current,
        next_retry_at: nil,
        error_message: 'Delivery worker stopped before the send result was recorded; not retried to prevent a duplicate message'
      )
    end
  end

  def enqueue_dispatch(campaign)
    return unless campaign.campaign_deliveries.dispatchable.exists?

    Campaigns::Whatsapp::DispatchBatchJob.perform_later(campaign)
  end
end
