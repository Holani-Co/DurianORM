class Whatsapp::CampaignAudienceSnapshotService
  INSERT_BATCH_SIZE = 500

  def initialize(campaign)
    @campaign = campaign
  end

  def perform
    return @campaign.campaign_deliveries if @campaign.audience_snapshot_at.present?

    audience_results = audience_service.results
    CampaignDelivery.transaction do
      audience_results.each_slice(INSERT_BATCH_SIZE) do |batch|
        # Attributes are built entirely from account-scoped, already-validated
        # audience results. Bulk insertion keeps a maximum-size campaign from
        # issuing 10,000 individual INSERT statements during its snapshot.
        # rubocop:disable Rails/SkipsModelValidations
        CampaignDelivery.insert_all!(batch.map { |result| delivery_attributes(result) })
        # rubocop:enable Rails/SkipsModelValidations
      end
      @campaign.update!(audience_snapshot_at: Time.current)
      @campaign.refresh_delivery_counts!
    end
    @campaign.campaign_deliveries
  end

  private

  def audience_service
    @audience_service ||= Whatsapp::CampaignAudienceService.new(
      account: @campaign.account,
      inbox: @campaign.inbox,
      audience: @campaign.audience
    )
  end

  def delivery_attributes(result)
    now = Time.current
    {
      account_id: @campaign.account_id,
      campaign_id: @campaign.id,
      contact_id: result.contact.id,
      whatsapp_consent_id: result.consent&.id,
      phone_number: result.phone_number.to_s.strip.presence,
      status: result.eligible? ? 'queued' : 'skipped',
      skip_reason: result.skip_reason,
      queued_at: result.eligible? ? Time.current : nil,
      recipient_snapshot: recipient_snapshot(result),
      template_parameters: @campaign.template_params || {},
      created_at: now,
      updated_at: now
    }
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
