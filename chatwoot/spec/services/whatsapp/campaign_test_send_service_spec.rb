require 'rails_helper'

describe Whatsapp::CampaignTestSendService do
  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:template) do
    WhatsappTemplate.create!(account: account, inbox: channel.inbox, name: 'test_send', language: 'en_US', category: 'MARKETING',
                             status: 'APPROVED', components: [{ 'type' => 'BODY', 'text' => 'Hello {{1}}' }])
  end
  let(:template_params) do
    { 'name' => template.name, 'language' => template.language, 'processed_params' => { 'body' => { '1' => 'Siddharth' } } }
  end

  it 'processes the local approved template without relying on the channel cache' do
    expect(channel).to receive(:send_template)
      .with('+919999990033', hash_including(name: 'test_send', lang_code: 'en_US'), nil)
      .and_return('wamid.test')

    result = described_class.new(inbox: channel.inbox, template: template, phone_number: '+919999990033',
                                 template_params: template_params).perform
    expect(result).to eq('wamid.test')
  end

  it 'rejects an invalid test recipient' do
    service = described_class.new(inbox: channel.inbox, template: template, phone_number: '99999', template_params: template_params)

    expect { service.perform }.to raise_error(described_class::Error, 'Enter a valid E.164 phone number')
  end
end
