# Periodically refreshes WhatsApp Cloud templates from Meta so approval-status
# changes (DRAFT/PENDING → APPROVED/REJECTED) are reflected even if a template
# status webhook is missed. Best-effort per channel — one channel's failure
# never blocks the others.
class Whatsapp::TemplatesSyncJob < ApplicationJob
  queue_as :scheduled_jobs

  def perform
    Channel::Whatsapp.where(provider: 'whatsapp_cloud').find_each do |channel|
      channel.sync_templates
    rescue StandardError => e
      Rails.logger.error("[whatsapp] scheduled template sync failed for channel #{channel.id}: #{e.message}")
    end
  end
end
