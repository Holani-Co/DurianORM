class Campaigns::Whatsapp::DispatchBatchJob < ApplicationJob
  # :deferred sits below :low (where inbound WhatsApp is processed), so campaign
  # fan-out yields to live customer message handling on a resource-limited box.
  queue_as :deferred

  BATCH_SIZE = ENV.fetch('WHATSAPP_CAMPAIGN_BATCH_SIZE', 100).to_i
  BATCH_INTERVAL = ENV.fetch('WHATSAPP_CAMPAIGN_BATCH_INTERVAL_SECONDS', 1).to_i.seconds
  # How long to wait before re-checking when the inbox is in a rate-limit cooldown.
  COOLDOWN_RECHECK_INTERVAL = ENV.fetch('WHATSAPP_CAMPAIGN_COOLDOWN_RECHECK_SECONDS', 30).to_i.seconds

  def perform(campaign, after_id = 0)
    return unless campaign.reload.execution_running?

    # Meta signalled rate limiting for this inbox — hold the fan-out (keeping our
    # place at after_id) and re-check shortly instead of piling on more sends.
    return schedule_recheck(campaign, after_id) if Campaigns::Whatsapp::RateLimitCooldown.cooling_down?(campaign.inbox_id)

    deliveries = campaign.campaign_deliveries.dispatchable.where('id > ?', after_id).order(:id).limit(BATCH_SIZE).to_a
    deliveries.each { |delivery| Campaigns::Whatsapp::SendDeliveryJob.perform_later(delivery) }
    schedule_next_batch(campaign, deliveries.last.id) if deliveries.size == BATCH_SIZE
  end

  private

  def schedule_next_batch(campaign, after_id)
    self.class.set(wait: BATCH_INTERVAL).perform_later(campaign, after_id)
  end

  def schedule_recheck(campaign, after_id)
    self.class.set(wait: COOLDOWN_RECHECK_INTERVAL).perform_later(campaign, after_id)
  end
end
