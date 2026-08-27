class Whatsapp::CampaignReplyAttributionService
  ATTRIBUTION_WINDOW = 7.days

  def initialize(inbox:, contact:, received_at: Time.current)
    @inbox = inbox
    @contact = contact
    @received_at = received_at
  end

  def perform
    delivery = attributable_delivery
    return if delivery.blank? || delivery.replied_at.present?

    delivery.update!(replied_at: @received_at)
    delivery.campaign.refresh_delivery_counts!
    delivery
  end

  private

  def attributable_delivery
    @contact.campaign_deliveries
            .joins(:campaign)
            .where(campaigns: { inbox_id: @inbox.id })
            .where.not(sent_at: nil)
            .where(sent_at: (@received_at - ATTRIBUTION_WINDOW)..@received_at)
            .order(sent_at: :desc)
            .first
  end
end
