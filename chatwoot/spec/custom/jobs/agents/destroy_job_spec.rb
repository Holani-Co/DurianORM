require 'rails_helper'

RSpec.describe Agents::DestroyJob do
  let(:account) { create(:account) }
  let(:user) { create(:user) }
  let(:inbox) { create(:inbox, account: account) }

  it 'leaves a re-added member untouched with OS_MANAGED on' do
    create(:account_user, account: account, user: user)
    create(:inbox_member, inbox: inbox, user: user)

    with_modified_env(OS_MANAGED: 'on') { described_class.perform_now(account, user) }

    expect(user.inbox_members.count).to eq(1)
  end

  it 'still strips a re-added member as today with OS_MANAGED unset' do
    create(:account_user, account: account, user: user)
    create(:inbox_member, inbox: inbox, user: user)

    described_class.perform_now(account, user)

    expect(user.inbox_members.count).to eq(0)
  end

  it 'ends sessions and rotates the API token once no account is left' do
    create(:inbox_member, inbox: inbox, user: user)
    user.create_new_auth_token
    old_token = user.access_token.token

    with_modified_env(OS_MANAGED: 'on') { described_class.perform_now(account, user) }

    expect(user.inbox_members.count).to eq(0)
    expect(user.reload.tokens).to eq({})
    expect(user.access_token.reload.token).not_to eq(old_token)
  end
end
