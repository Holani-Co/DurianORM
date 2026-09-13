require 'rails_helper'

describe Campaigns::Whatsapp::SendDeliveryJob do
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:channel) do
    create(:channel_whatsapp, account: account, provider: 'whatsapp_cloud', validate_provider_config: false, sync_templates: false)
  end
  let(:template) do
    WhatsappTemplate.create!(account: account, inbox: channel.inbox, name: 'delivery_spec', language: 'en_US', category: 'MARKETING',
                             status: 'APPROVED', components: [{ 'type' => 'BODY', 'text' => 'Hello {{1}}' }])
  end
  let(:campaign) do
    create(:campaign, account: account, inbox: channel.inbox, whatsapp_template: template, execution_status: :running,
                      template_params: { 'name' => template.name, 'language' => template.language,
                                         'processed_params' => { 'body' => { '1' => 'Customer' } } })
  end
  let(:contact) { create(:contact, :with_phone_number, account: account) }
  let(:delivery) do
    CampaignDelivery.create!(account: account, campaign: campaign, contact: contact, phone_number: contact.phone_number,
                             status: 'queued', recipient_snapshot: {}, template_parameters: campaign.template_params)
  end

  it 'sends from the immutable delivery snapshot and records the Meta ID' do
    stub_request(:post, /graph\.facebook\.com.*messages/)
      .to_return(status: 200, body: { messages: [{ id: 'wamid.delivery' }] }.to_json,
                 headers: { 'Content-Type' => 'application/json' })

    described_class.perform_now(delivery)

    expect(delivery.reload).to be_status_sent
    expect(delivery).to have_attributes(meta_message_id: 'wamid.delivery', attempt_count: 1)
    expect(campaign.reload).to be_execution_completed
  end

  it 'queues an exponential retry after a transient failure' do
    stub_request(:post, /graph\.facebook\.com.*messages/).to_return(status: 500, body: '{}')

    expect { described_class.perform_now(delivery) }.to have_enqueued_job(described_class)
    expect(delivery.reload).to be_status_queued
    expect(delivery).to have_attributes(attempt_count: 1)
    expect(delivery.next_retry_at).to be_present
  end

  it 'fails and finalizes after the retry budget is exhausted' do
    delivery.update!(attempt_count: 2)
    stub_request(:post, /graph\.facebook\.com.*messages/).to_return(status: 500, body: '{}')

    described_class.perform_now(delivery)

    expect(delivery.reload).to be_status_failed
    expect(delivery.attempt_count).to eq(3)
    expect(campaign.reload).to be_execution_completed
  end

  it 'captures the Meta error code and message on a permanent failure' do
    delivery.update!(attempt_count: 2)
    error_body = { error: { code: 131_026, message: 'Message undeliverable',
                            error_data: { details: 'Recipient cannot receive marketing messages' } } }
    stub_request(:post, /graph\.facebook\.com.*messages/)
      .to_return(status: 400, body: error_body.to_json, headers: { 'Content-Type' => 'application/json' })

    described_class.perform_now(delivery)

    expect(delivery.reload).to be_status_failed
    expect(delivery.error_code).to eq('131026')
    expect(delivery.error_message).to eq('Recipient cannot receive marketing messages')
  end

  it 'backs off a rate-limit response without consuming the fast-retry budget and trips the cooldown' do
    # attempt_count 2 → 3 after claim: a generic error would fail here (>= MAX_ATTEMPTS),
    # so staying queued proves the rate-limit path (RATE_LIMIT_MAX_ATTEMPTS) was taken.
    delivery.update!(attempt_count: 2)
    error_body = { error: { code: 130_429, message: 'Rate limit hit' } }
    stub_request(:post, /graph\.facebook\.com.*messages/)
      .to_return(status: 400, body: error_body.to_json, headers: { 'Content-Type' => 'application/json' })

    expect(Campaigns::Whatsapp::RateLimitCooldown).to receive(:trip!).with(campaign.inbox_id, anything)
    expect { described_class.perform_now(delivery) }.to have_enqueued_job(described_class)

    expect(delivery.reload).to be_status_queued
    expect(delivery.error_code).to eq('130429')
    expect(delivery.next_retry_at).to be_present
  end
end
