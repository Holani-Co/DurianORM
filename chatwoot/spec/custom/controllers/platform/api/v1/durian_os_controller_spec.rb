require 'rails_helper'

RSpec.describe 'Platform Durian OS API', type: :request do
  let(:platform_app) { create(:platform_app) }
  let(:headers) { { api_access_token: platform_app.access_token.token } }
  let(:user) { create(:user) }

  describe 'GET /platform/api/v1/durian_os/status' do
    it 'needs the Platform token' do
      get '/platform/api/v1/durian_os/status'
      expect(response).to have_http_status(:unauthorized)
    end

    it 'reports the emergency login off by default' do
      get '/platform/api/v1/durian_os/status', headers: headers, as: :json
      expect(response).to have_http_status(:success)
      expect(response.parsed_body).to eq('emergencyLogin' => { 'enabled' => false })
    end

    it 'reports it on while it is on' do
      env = { OS_EMERGENCY_EMAIL: 'emergency@orm.test', OS_EMERGENCY_LOGIN_UNTIL: 1.hour.from_now.utc.iso8601,
              OS_EMERGENCY_PASSWORD_HASH: BCrypt::Password.create('x' * 24, cost: 12).to_s }
      with_modified_env(env) { get '/platform/api/v1/durian_os/status', headers: headers, as: :json }
      expect(response.parsed_body).to eq('emergencyLogin' => { 'enabled' => true })
    end
  end

  describe 'POST /platform/api/v1/durian_os/users/:id/end_sessions' do
    it 'refuses a user the Platform App has no rights over' do
      post "/platform/api/v1/durian_os/users/#{user.id}/end_sessions", headers: headers, as: :json
      expect(response).to have_http_status(:unauthorized)
    end

    it 'clears every session and rotates the API token' do
      create(:platform_app_permissible, platform_app: platform_app, permissible: user)
      user.create_new_auth_token
      user.create_new_auth_token
      old_token = user.access_token.token

      post "/platform/api/v1/durian_os/users/#{user.id}/end_sessions", headers: headers, as: :json

      expect(response).to have_http_status(:success)
      expect(response.parsed_body).to eq('sessionsEnded' => 2, 'tokensRevoked' => 1)
      expect(user.reload.tokens).to eq({})
      expect(user.access_token.reload.token).not_to eq(old_token)
    end
  end

  describe 'GET /platform/api/v1/users/:id/login for the emergency user' do
    let(:emergency_user) { create(:user, custom_attributes: { 'os_kind' => 'emergency' }) }

    before { create(:platform_app_permissible, platform_app: platform_app, permissible: emergency_user) }

    it 'still gives a link with the switch unset' do
      get "/platform/api/v1/users/#{emergency_user.id}/login", headers: headers, as: :json
      expect(response).to have_http_status(:success)
    end

    it 'refuses with the switch on' do
      with_modified_env(OS_SIGNIN_ONLY: 'on') { get "/platform/api/v1/users/#{emergency_user.id}/login", headers: headers, as: :json }
      expect(response).to have_http_status(:forbidden)
    end

    it 'hands out no API token for it with the switch on' do
      with_modified_env(OS_SIGNIN_ONLY: 'on') { post "/platform/api/v1/users/#{emergency_user.id}/token", headers: headers, as: :json }
      expect(response).to have_http_status(:forbidden)
      expect(response.body).not_to include(emergency_user.access_token.token)
    end
  end
end
