require 'rails_helper'

describe Whatsapp::CampaignReplyAttributionService do
  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:inbox) { channel.inbox }
  let(:contact) { create(:contact, :with_phone_number, account: account) }
  let(:campaign) { create(:campaign, account: account, inbox: inbox) }

  it 'attributes a reply to the most recent send in the seven-day window' do
    older = CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                                     status: 'sent', sent_at: 2.days.ago, recipient_snapshot: {}, template_parameters: {})
    recent_campaign = create(:campaign, account: account, inbox: inbox)
    recent = CampaignDelivery.create!(account: account, campaign: recent_campaign, contact: contact, phone_number: contact.phone_number,
                                      status: 'sent', sent_at: 1.hour.ago, recipient_snapshot: {}, template_parameters: {})

    described_class.new(inbox: inbox, contact: contact).perform

    expect(recent.reload.replied_at).to be_present
    expect(older.reload.replied_at).to be_nil
    expect(recent_campaign.reload.reply_count).to eq(1)

    described_class.new(inbox: inbox, contact: contact).perform
    expect(older.reload.replied_at).to be_nil
  end

  it 'does not attribute replies after the window' do
    delivery = CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                                        status: 'sent', sent_at: 8.days.ago, recipient_snapshot: {}, template_parameters: {})

    expect(described_class.new(inbox: inbox, contact: contact).perform).to be_nil
    expect(delivery.reload.replied_at).to be_nil
  end
end
