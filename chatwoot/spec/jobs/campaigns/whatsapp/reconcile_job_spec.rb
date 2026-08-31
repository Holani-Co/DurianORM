require 'rails_helper'

describe Campaigns::Whatsapp::ReconcileJob do
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:campaign) { create(:campaign, account: account, inbox: channel.inbox, execution_status: :running) }
  let(:contact) { create(:contact, :with_phone_number, account: account) }

  it 'fails an ambiguous stale send instead of risking a duplicate message' do
    delivery = CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                                        status: 'sending', recipient_snapshot: {}, template_parameters: {})
    delivery.update_column(:updated_at, 31.minutes.ago) # rubocop:disable Rails/SkipsModelValidations

    described_class.perform_now

    expect(delivery.reload).to be_status_failed
    expect(delivery.error_message).to include('not retried to prevent a duplicate')
    expect(campaign.reload).to be_execution_completed
  end

  it 're-enqueues dispatch for a running campaign with queued recipients' do
    CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                             status: 'queued', recipient_snapshot: {}, template_parameters: {})

    expect { described_class.perform_now }
      .to have_enqueued_job(Campaigns::Whatsapp::DispatchBatchJob).with(campaign)
  end
end
