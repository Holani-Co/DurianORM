require 'rails_helper'

describe Whatsapp::ConsentCaptureService do
  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:contact) { create(:contact, :with_phone_number, account: account) }

  def perform_with(text, id: SecureRandom.uuid)
    described_class.new(inbox: channel.inbox, contact: contact, message_payload: { id: id, text: { body: text } }).perform
  end

  %w[STOP stop Unsubscribe CANCEL QUIT END DND REMOVE OPTOUT].each do |word|
    it "records an opt-out for '#{word}'" do
      consent = perform_with(word)
      expect(consent).to be_present
      expect(consent.status).to eq('OPTED_OUT')
    end
  end

  it 'records an opt-out for multi-word phrases and prefixes' do
    expect(perform_with('opt out').status).to eq('OPTED_OUT')
    expect(perform_with('STOP ALL').status).to eq('OPTED_OUT')
    expect(perform_with('remove me please').status).to eq('OPTED_OUT')
  end

  it 'records an opt-in for START / RESUME' do
    expect(perform_with('START').status).to eq('OPTED_IN')
    expect(perform_with('resume').status).to eq('OPTED_IN')
  end

  it 'ignores unrelated messages' do
    expect(perform_with('what is the price?')).to be_nil
  end

  it 'cancels pending campaign deliveries on opt-out' do
    template = WhatsappTemplate.create!(account: account, inbox: channel.inbox, name: 'consent_spec', language: 'en_US',
                                        category: 'MARKETING', status: 'APPROVED',
                                        components: [{ 'type' => 'BODY', 'text' => 'Hi {{1}}' }])
    campaign = create(:campaign, account: account, inbox: channel.inbox, whatsapp_template: template, execution_status: :running,
                                 template_params: { 'name' => template.name, 'language' => template.language })
    delivery = CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                                        status: 'queued', recipient_snapshot: {}, template_parameters: {})

    perform_with('STOP')

    expect(delivery.reload).to be_status_cancelled
    expect(delivery.skip_reason).to eq('opted_out')
  end
end
