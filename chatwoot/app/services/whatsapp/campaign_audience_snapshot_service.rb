class Whatsapp::CampaignAudienceSnapshotService
  def initialize(campaign)
    @campaign = campaign
  end

  def perform
    return @campaign.campaign_deliveries if @campaign.audience_snapshot_at.present?

    audience_results = audience_service.results
    CampaignDelivery.transaction do
      audience_results.each { |result| create_delivery!(result) }
      @campaign.update!(audience_snapshot_at: Time.current)
      @campaign.refresh_delivery_counts!
    end
    @campaign.campaign_deliveries.reload
  end

  private

  def audience_service
    @audience_service ||= Whatsapp::CampaignAudienceService.new(
      account: @campaign.account,
      inbox: @campaign.inbox,
      audience: @campaign.audience
    )
  end

  def create_delivery!(result)
    @campaign.campaign_deliveries.create!(
      account: @campaign.account,
      contact: result.contact,
      whatsapp_consent: result.consent,
      phone_number: result.phone_number,
      status: result.eligible? ? 'queued' : 'skipped',
      skip_reason: result.skip_reason,
      queued_at: result.eligible? ? Time.current : nil,
      recipient_snapshot: recipient_snapshot(result),
      template_parameters: @campaign.template_params || {}
    )
  end

  def recipient_snapshot(result)
    {
      contact_id: result.contact.id,
      name: result.contact.name,
      phone_number: result.phone_number,
      consent_status: result.consent&.status,
      consent_recorded_at: result.consent&.recorded_at
    }
  end
end
