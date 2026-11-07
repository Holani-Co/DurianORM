require 'rails_helper'

# The ceiling of a Durian OS AI connection (docs/mcp/MCP.md, section 4): only the caller's own rows, only below
# administer, only an administrator, and nothing at all without DurianOs::Current.ai.
RSpec.describe Custom::AccountUser do
  let(:account) { create(:account) }
  let(:tara) { create(:user, account: account, role: :administrator, name: 'Tara Fern', email: 'tara.fern@orm.test') }
  let(:omar) { create(:user, account: account, role: :agent, name: 'Omar Quill', email: 'omar.quill@orm.test') }
  let(:tara_row) { tara.account_users.find_by(account: account) }
  let(:omar_row) { omar.account_users.find_by(account: account) }

  def as_ai(user, ceiling, &)
    caller = DurianOs::Ai::Caller.new(user: user, sub: SecureRandom.uuid, client: 'Claude', ceiling: ceiling, ref: 'Ab3dEf')
    DurianOs::Current.set(ai: caller, &)
  end

  it 'lowers the caller\'s own administrator row to a plain agent below administer' do
    role = create(:custom_role, account: account, permissions: ['report_manage'])
    tara_row.update!(custom_role: role)

    %w[edit view].each do |ceiling|
      as_ai(tara, ceiling) do
        row = AccountUser.find(tara_row.id)
        expect(row.role).to eq('agent')
        expect(row.administrator?).to be(false)
        expect(row.agent?).to be(true)
        expect(row.custom_role_id).to be_nil
        expect(row.custom_role).to be_nil
        expect(row.permissions).to eq(['agent'])
        expect(row[:role]).to eq('administrator')
      end
    end
  end

  it 'is what User#administrator? and #assigned_inboxes see' do
    inbox = create(:inbox, account: account)
    create(:inbox, account: account)
    create(:inbox_member, user: tara, inbox: inbox)
    Current.account = account
    as_ai(tara, 'edit') do
      person = User.find(tara.id)
      expect(person.administrator?).to be(false)
      expect(person.role).to eq('agent')
      expect(person.assigned_inboxes.pluck(:id)).to eq([inbox.id])
    end
    expect(User.find(tara.id).assigned_inboxes.count).to eq(2)
  ensure
    Current.reset
  end

  it 'keeps an administrator at administer' do
    as_ai(tara, 'administer') do
      row = AccountUser.find(tara_row.id)
      expect(row.role).to eq('administrator')
      expect(row.administrator?).to be(true)
      expect(row.permissions).to eq(['administrator'])
    end
  end

  it 'never changes other people\'s rows' do
    as_ai(omar, 'view') do
      expect(AccountUser.find(tara_row.id).administrator?).to be(true)
    end
  end

  it 'leaves an agent and their custom role as they are' do
    role = create(:custom_role, account: account, permissions: ['conversation_manage'])
    omar_row.update!(custom_role: role)
    as_ai(omar, 'view') do
      row = AccountUser.find(omar_row.id)
      expect(row.role).to eq('agent')
      expect(row.custom_role).to eq(role)
      expect(row.permissions).to eq(%w[conversation_manage custom_role])
    end
  end

  it 'does nothing without an AI caller' do
    expect(DurianOs::Current.ai).to be_nil
    expect(AccountUser.find(tara_row.id).administrator?).to be(true)
  end

  it 'never stores the lowered role' do
    as_ai(tara, 'view') do
      ActiveRecord::Base.while_preventing_writes(false) { AccountUser.find(tara_row.id).update!(availability: 'busy') }
    end
    expect(tara_row.reload.role).to eq('administrator')
    expect(tara_row.availability).to eq('busy')
  end
end
