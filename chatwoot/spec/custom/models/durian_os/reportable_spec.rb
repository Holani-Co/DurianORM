require 'rails_helper'

RSpec.describe DurianOs::Reportable do
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:other_account) { create(:account) }
  let(:user) { create(:user) }
  let(:reporter_env) do
    { OS_REPORT_URL: 'https://durianos.test/api/os/v2/changes', OS_REPORT_KEY: 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8',
      OS_REPORT_KEY_ID: 'orm-2026-10', OS_ACCOUNT_ID: account.id.to_s }
  end

  def reported_kinds
    enqueued_jobs.select { |job| job[:job] == DurianOs::ChangeReportJob }
                 .flat_map { |job| JSON.parse(job[:args].first)['changes'].pluck('kind', 'localUserId') }
  end

  around do |example|
    with_modified_env(reporter_env) { example.run }
  end

  before { clear_enqueued_jobs }

  it 'reports person.created when the person is added to the account' do
    create(:account_user, account: account, user: user)
    expect(reported_kinds).to include(['person.created', user.id.to_s])
  end

  it 'reports person.changed on a role change, and nothing on availability' do
    account_user = create(:account_user, account: account, user: user, role: :agent)
    clear_enqueued_jobs
    account_user.update!(availability: :busy)
    expect(reported_kinds).to be_empty
    account_user.update!(role: :administrator)
    expect(reported_kinds).to eq([['person.changed', user.id.to_s]])
  end

  it 'reports person.deactivated when the membership is removed' do
    account_user = create(:account_user, account: account, user: user)
    clear_enqueued_jobs
    account_user.destroy!
    expect(reported_kinds).to include(['person.deactivated', user.id.to_s])
  end

  it 'reports person.changed for inbox and team membership' do
    inbox = create(:inbox, account: account)
    team = create(:team, account: account)
    create(:account_user, account: account, user: user)
    clear_enqueued_jobs

    create(:inbox_member, inbox: inbox, user: user)
    create(:team_member, team: team, user: user)
    expect(reported_kinds).to eq([['person.changed', user.id.to_s]] * 2)
  end

  it 'ignores every other account' do
    create(:account_user, account: other_account, user: user)
    create(:inbox_member, inbox: create(:inbox, account: other_account), user: user)
    expect(reported_kinds).to be_empty
  end

  it 'reports nothing while the reporter is not configured' do
    with_modified_env OS_REPORT_URL: nil do
      create(:account_user, account: account, user: user)
    end
    expect(reported_kinds).to be_empty
  end
end
