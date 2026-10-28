require 'rails_helper'

RSpec.describe Reports::SendOwnerSyncAlertJob do
  let(:report) do
    {
      'synced_at' => '2026-10-29T08:45:00+05:30',
      'problems' => [
        { 'kind' => 'routing', 'group' => 'govt bulk', 'label' => 'Uttrakhand and Punjab', 'owner_id' => '278',
          'owner_name' => 'RAJ KUMAR SINGH', 'owner_email' => 'raj@example.com', 'status' => 'deleted', 'successor' => nil },
        { 'kind' => 'store', 'group' => 'stores', 'label' => 'Darbhanga', 'owner_id' => '333',
          'owner_name' => 'Darbhanga Dealer', 'owner_email' => 'd@example.com', 'status' => 'deactivated',
          'successor' => { 'name' => 'Franchise Team', 'email' => 'fr@example.com', 'reason' => 'their reporting manager' } }
      ]
    }
  end

  # Never deliver for real from a spec (the local test env can reach a live MTA).
  around do |example|
    method = ActionMailer::Base.delivery_method
    ActionMailer::Base.delivery_method = :test
    example.run
  ensure
    ActionMailer::Base.delivery_method = method
  end

  before do
    ActionMailer::Base.deliveries.clear
    allow(HTTParty).to receive(:get).and_return(
      instance_double(HTTParty::Response, success?: true, parsed_response: report)
    )
  end

  it 'emails the inactive owners and who gets their deals now' do
    with_modified_env OWNER_SYNC_ALERT_RECIPIENTS: 'ops@example.com', SMTP_ADDRESS: 'smtp.example.com' do
      described_class.perform_now
    end
    mail = ActionMailer::Base.deliveries.last
    expect(mail.to).to eq(['ops@example.com'])
    expect(mail.subject).to include('2 deal owner(s) inactive')
    expect(mail.html_part&.body&.decoded || mail.body.decoded).to include('RAJ KUMAR SINGH', 'Franchise Team')
  end

  it 'sends nothing without recipients or problems' do
    with_modified_env OWNER_SYNC_ALERT_RECIPIENTS: '', SMTP_ADDRESS: 'smtp.example.com' do
      described_class.perform_now
    end
    report['problems'] = []
    with_modified_env OWNER_SYNC_ALERT_RECIPIENTS: 'ops@example.com', SMTP_ADDRESS: 'smtp.example.com' do
      described_class.perform_now
    end
    expect(ActionMailer::Base.deliveries).to be_empty
  end
end
