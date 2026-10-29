require 'rails_helper'

RSpec.describe 'Agents API under Durian OS', type: :request do
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:admin) { create(:user, account: account, role: :administrator) }
  let(:agent) { create(:user, account: account, role: :agent) }
  let(:headers) { admin.create_new_auth_token }

  describe 'DELETE /api/v1/accounts/:account_id/agents/:id' do
    it 'deletes the user row as today with OS_MANAGED unset' do
      delete "/api/v1/accounts/#{account.id}/agents/#{agent.id}", headers: headers, as: :json
      expect(response).to have_http_status(:success)
      expect(DeleteObjectJob).to have_been_enqueued.with(agent)
    end

    it 'keeps the user row with OS_MANAGED on' do
      with_modified_env(OS_MANAGED: 'on') { delete "/api/v1/accounts/#{account.id}/agents/#{agent.id}", headers: headers, as: :json }
      expect(response).to have_http_status(:success)
      expect(DeleteObjectJob).not_to have_been_enqueued
      expect(account.account_users.exists?(user_id: agent.id)).to be(false)
      expect(User.exists?(agent.id)).to be(true)
    end
  end

  describe 'service and emergency rows' do
    let(:service) { create(:user, account: account, role: :administrator, custom_attributes: { 'os_kind' => 'service' }) }

    it 'refuses to edit or remove them with OS_MANAGED on' do
      with_modified_env OS_MANAGED: 'on' do
        patch "/api/v1/accounts/#{account.id}/agents/#{service.id}", params: { role: 'agent' }, headers: headers, as: :json
        expect(response).to have_http_status(:unprocessable_entity)
        delete "/api/v1/accounts/#{account.id}/agents/#{service.id}", headers: headers, as: :json
        expect(response).to have_http_status(:unprocessable_entity)
      end
      expect(service.account_users.find_by(account: account).role).to eq('administrator')
    end
  end

  describe 'the emergency user API token' do
    let(:emergency_user) { create(:user, account: account, role: :administrator, custom_attributes: { 'os_kind' => 'emergency' }) }

    it 'works as today with the switch unset' do
      get "/api/v1/accounts/#{account.id}/agents", headers: { api_access_token: emergency_user.access_token.token }, as: :json
      expect(response).to have_http_status(:success)
    end

    it 'is refused with the switch on' do
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        get "/api/v1/accounts/#{account.id}/agents", headers: { api_access_token: emergency_user.access_token.token }, as: :json
      end
      expect(response).to have_http_status(:unauthorized)
    end

    it 'cannot be reset with the switch on, even in an emergency session' do
      emergency = { OS_SIGNIN_ONLY: 'on', OS_EMERGENCY_EMAIL: emergency_user.email, OS_EMERGENCY_LOGIN_UNTIL: 1.hour.from_now.utc.iso8601,
                    OS_EMERGENCY_PASSWORD_HASH: BCrypt::Password.create('x' * 24, cost: 12).to_s }
      with_modified_env emergency do
        session = emergency_user.create_new_auth_token
        post '/api/v1/profile/reset_access_token', headers: session, as: :json
        expect(response).to have_http_status(:forbidden)
        put '/api/v1/profile', params: { profile: { name: 'Changed' } }, headers: session, as: :json
        expect(response).to have_http_status(:forbidden)
      end
      expect(emergency_user.reload.name).not_to eq('Changed')
    end
  end
end
