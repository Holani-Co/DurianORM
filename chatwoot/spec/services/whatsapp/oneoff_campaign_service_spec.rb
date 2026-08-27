require 'rails_helper'

describe Whatsapp::OneoffCampaignService do
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:inbox) { channel.inbox }
  let(:label) { create(:label, account: account) }
  let(:template_params) do
    {
      'name' => 'campaign_template',
      'language' => 'en_US',
      'processed_params' => { 'body' => { '1' => 'Customer' } }
    }
  end
  let(:template) do
    WhatsappTemplate.create!(
      account: account,
      inbox: inbox,
      name: 'campaign_template',
      language: 'en_US',
      category: 'MARKETING',
      status: 'APPROVED',
      components: [{ 'type' => 'BODY', 'text' => 'Hello {{1}}' }]
    )
  end
  let(:campaign) do
    create(
      :campaign,
      account: account,
      inbox: inbox,
      audience: [{ 'type' => 'Label', 'id' => label.id }],
      template_params: template_params,
      whatsapp_template: template
    )
  end

  before { account.enable_features!(:whatsapp_campaign) }

  describe '#perform' do
    it 'claims the campaign, snapshots consented recipients, and queues dispatch' do
      eligible = create(:contact, :with_phone_number, account: account)
      skipped = create(:contact, account: account, phone_number: nil)
      eligible.update_labels([label.title])
      skipped.update_labels([label.title])
      WhatsappConsent.create!(
        account: account,
        inbox: inbox,
        contact: eligible,
        purpose: 'MARKETING',
        status: 'OPTED_IN',
        source: 'spec',
        recorded_at: Time.current,
        details: {}
      )

      expect { described_class.new(campaign: campaign).perform }
        .to have_enqueued_job(Campaigns::Whatsapp::DispatchBatchJob).with(campaign)

      expect(campaign.reload).to be_execution_running
      expect(campaign.campaign_deliveries.group(:status).count).to eq('queued' => 1, 'skipped' => 1)
      expect(campaign).to have_attributes(audience_count: 2, eligible_count: 1, skipped_count: 1)
    end

    it 'is idempotent when the scheduler claims the same campaign twice' do
      campaign.update!(execution_status: :queued)

      expect { described_class.new(campaign: campaign).perform }
        .not_to have_enqueued_job(Campaigns::Whatsapp::DispatchBatchJob)
      expect(campaign.campaign_deliveries.count).to eq(0)
    end

    it 'completes a campaign when every audience member is skipped' do
      contact = create(:contact, account: account, phone_number: nil)
      contact.update_labels([label.title])

      described_class.new(campaign: campaign).perform

      expect(campaign.reload).to be_execution_completed
      expect(campaign).to be_completed
      expect(campaign.campaign_deliveries.first).to be_status_skipped
    end

    it 'requires an approved local template' do
      campaign.update!(whatsapp_template: nil)

      expect { described_class.new(campaign: campaign).perform }
        .to raise_error('Approved WhatsApp template required')
    end

    it 'requires the WhatsApp campaign feature' do
      account.disable_features!(:whatsapp_campaign)

      expect { described_class.new(campaign: campaign).perform }
        .to raise_error('WhatsApp campaigns feature not enabled')
    end

    it 'requires a WhatsApp Cloud inbox' do
      channel.update!(provider: 'default')

      expect { described_class.new(campaign: campaign).perform }
        .to raise_error('WhatsApp Cloud provider required')
    end
  end
end
