require 'rails_helper'

describe Whatsapp::CampaignAudienceService do
  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:inbox) { channel.inbox }
  let(:label) { create(:label, account: account) }
  let(:audience) { [{ 'type' => 'Label', 'id' => label.id }] }

  def tagged_contact(phone_number: nil)
    contact = create(:contact, account: account, phone_number: phone_number)
    contact.update_labels([label.title])
    contact
  end

  it 'requires the latest explicit marketing opt-in' do
    contact = tagged_contact(phone_number: '+919999990031')
    opted_in = WhatsappConsent.create!(account: account, inbox: inbox, contact: contact, status: 'OPTED_IN',
                                       purpose: 'MARKETING', source: 'spec', recorded_at: 1.minute.ago, details: {})

    result = described_class.new(account: account, inbox: inbox, audience: audience).results.first
    expect(result).to be_eligible
    expect(result.consent).to eq(opted_in)

    WhatsappConsent.create!(account: account, inbox: inbox, contact: contact, status: 'OPTED_OUT',
                            purpose: 'MARKETING', source: 'spec', recorded_at: Time.current, details: {})
    expect(described_class.new(account: account, inbox: inbox, audience: audience).results.first.skip_reason).to eq('opted_out')
  end

  it 'reports missing and invalid phone numbers as skipped' do
    tagged_contact
    invalid_phone = tagged_contact(phone_number: '+919999990032')
    invalid_phone.update_column(:phone_number, '12345') # rubocop:disable Rails/SkipsModelValidations

    summary = described_class.new(account: account, inbox: inbox, audience: audience).summary
    expect(summary[:reasons]).to eq('missing_phone_number' => 1, 'invalid_phone_number' => 1)
  end

  it 'stops snapshots above the configured audience cap' do
    stub_const('Whatsapp::CampaignAudienceService::DEFAULT_MAX_AUDIENCE', 1)
    2.times { tagged_contact }
    service = described_class.new(account: account, inbox: inbox, audience: audience)

    expect(service.summary).to include(limit_exceeded: true, max_audience_count: 1, audience_count: 2)
    expect { service.results }.to raise_error(described_class::AudienceLimitExceeded)
  end
end
