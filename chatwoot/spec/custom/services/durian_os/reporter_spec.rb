require 'rails_helper'

RSpec.describe DurianOs::Reporter do
  include ActiveJob::TestHelper

  let(:vector_key) { 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8' }
  let(:account) { create(:account) }
  let(:reporter_env) do
    { OS_REPORT_URL: 'https://durianos.test/api/os/v2/changes', OS_REPORT_KEY: vector_key,
      OS_REPORT_KEY_ID: 'orm-2026-10', OS_ACCOUNT_ID: account.id.to_s }
  end

  describe '.signature' do
    it 'matches the contract 3.6 test vector' do
      body = '{"reportId":"2c5ea4c0-4067-4e19-8bad-9b1deb4d3b7d","app":"tenders","sentAt":"2026-10-28T10:02:00Z",' \
             '"changes":[{"kind":"ping","at":"2026-10-28T10:02:00Z"}]}'
      key = Base64.urlsafe_decode64("#{vector_key}=")

      expect(Digest::SHA256.hexdigest(body)).to eq('458364dc27bc3f2af7fe262b0282305b831ba1991ebae513ab61ada84755e6bd')
      expect(described_class.signature(body, 1_793_181_720, key: key, key_id: 'tenders-2026-10', app: 'tenders'))
        .to eq('v1=b20dbb033f760188e625be995ffd24b331a19cce5f8df0834f175d8b836c3395')
    end
  end

  describe '.report_person' do
    let(:user) { create(:user, custom_attributes: { 'os_person_id' => '0f8c2a3e-5b7d-4e21-9a61-2c4d8e9f1a03' }) }

    it 'queues nothing while the reporter is not configured' do
      with_modified_env OS_REPORT_URL: nil, OS_REPORT_KEY: nil, OS_REPORT_KEY_ID: nil do
        expect { described_class.report_person('person.changed', user.id) }.not_to have_enqueued_job(DurianOs::ChangeReportJob)
      end
    end

    it 'queues a report that names the change and carries no access data' do
      create(:account_user, account: account, user: user, role: :agent)
      with_modified_env reporter_env do
        clear_enqueued_jobs
        described_class.report_person('person.changed', user.id)
        body = JSON.parse(enqueued_jobs.last[:args].first)

        expect(body['app']).to eq('orm')
        expect(body['reportId']).to match(/\A\h{8}-\h{4}-4\h{3}-\h{4}-\h{12}\z/)
        change = body['changes'].first
        expect(change.keys).to match_array(%w[kind localUserId osPersonId version actor at])
        expect(change['localUserId']).to eq(user.id.to_s)
        expect(change['osPersonId']).to eq('0f8c2a3e-5b7d-4e21-9a61-2c4d8e9f1a03')
        expect(change['version']).to eq(DurianOs::AccessSnapshot.new(user.id, account.id).version)
        expect(change['actor']).to eq('via' => 'app_system')
      end
    end

    it 'names the admin acting in the app' do
      admin = create(:user)
      with_modified_env reporter_env do
        Current.user = admin
        clear_enqueued_jobs
        described_class.report_person('person.changed', user.id)
        change = JSON.parse(enqueued_jobs.last[:args].first)['changes'].first
        expect(change['actor']).to eq('localUserId' => admin.id.to_s, 'email' => admin.email, 'via' => 'app_admin')
      ensure
        Current.reset
      end
    end
  end

  describe DurianOs::AccessSnapshot do
    it 'computes the version over state, role, custom role, inboxes and teams' do
      expected = Base64.urlsafe_encode64(Digest::SHA256.digest('active|agent||3,7,12|2'), padding: false)[0, 16]
      version = described_class.version_for(state: 'active', role: 'agent', custom_role_id: nil, inbox_ids: [12, 3, 7], team_ids: [2])
      expect(version).to eq(expected)
      expect(version.length).to eq(16)
    end

    it 'reads a non-member as an inactive agent' do
      user = create(:user)
      snapshot = described_class.new(user.id, account.id)
      expect([snapshot.state, snapshot.role]).to eq(%w[inactive agent])
    end

    it 'reads offboarded from os_offboarded_at' do
      user = create(:user, custom_attributes: { 'os_offboarded_at' => '2026-10-29T10:00:00Z' })
      expect(described_class.new(user.id, account.id).state).to eq('offboarded')
    end
  end
end
