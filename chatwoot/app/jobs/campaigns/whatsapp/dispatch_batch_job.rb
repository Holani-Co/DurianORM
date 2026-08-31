class Campaigns::Whatsapp::DispatchBatchJob < ApplicationJob
  # :deferred sits below :low (where inbound WhatsApp is processed), so campaign
  # fan-out yields to live customer message handling on a resource-limited box.
  queue_as :deferred

  BATCH_SIZE = ENV.fetch('WHATSAPP_CAMPAIGN_BATCH_SIZE', 100).to_i
  BATCH_INTERVAL = ENV.fetch('WHATSAPP_CAMPAIGN_BATCH_INTERVAL_SECONDS', 1).to_i.seconds

  def perform(campaign, after_id = 0)
    return unless campaign.reload.execution_running?

    deliveries = campaign.campaign_deliveries.dispatchable.where('id > ?', after_id).order(:id).limit(BATCH_SIZE).to_a
    deliveries.each { |delivery| Campaigns::Whatsapp::SendDeliveryJob.perform_later(delivery) }
    schedule_next_batch(campaign, deliveries.last.id) if deliveries.size == BATCH_SIZE
  end

  private

  def schedule_next_batch(campaign, after_id)
    self.class.set(wait: BATCH_INTERVAL).perform_later(campaign, after_id)
  end
end
