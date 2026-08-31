class Whatsapp::CampaignDeliveryStatusService
  STATUS_TIMESTAMPS = {
    'sent' => :sent_at,
    'delivered' => :delivered_at,
    'read' => :read_at,
    'failed' => :failed_at
  }.freeze
  STATUS_RANK = { 'sent' => 1, 'delivered' => 2, 'read' => 3, 'failed' => 4 }.freeze

  def initialize(status_payload)
    @status_payload = status_payload
  end

  def perform
    delivery = CampaignDelivery.find_by(meta_message_id: @status_payload[:id])
    return if delivery.blank?

    status = @status_payload[:status]
    return unless STATUS_TIMESTAMPS.key?(status)
    return if stale_status?(delivery, status)

    attributes = { status: status }.merge(STATUS_TIMESTAMPS.fetch(status) => Time.current)
    add_error_attributes(attributes) if status == 'failed'
    delivery.update!(attributes)
    delivery.campaign.refresh_delivery_counts!
    Whatsapp::CampaignFinalizeService.new(delivery.campaign).perform
  end

  private

  def stale_status?(delivery, status)
    return false if status == 'failed'

    STATUS_RANK.fetch(status) < STATUS_RANK.fetch(delivery.status, 0)
  end

  def add_error_attributes(attributes)
    error = @status_payload[:errors]&.first
    return if error.blank?

    attributes[:error_code] = error[:code].to_s
    attributes[:error_message] = error[:title] || error[:message]
  end
end
