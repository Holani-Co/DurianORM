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

  describe 'POST /platform/api/v1/durian_os/users/:id/view_as_login' do
    let(:account) { create(:account) }
    let(:person) { create(:user, account: account) }
    let(:path) { "/platform/api/v1/durian_os/users/#{person.id}/view_as_login" }
    let(:body) { { by_email: 'Admin@Durian.test', by_name: 'Asha Admin', imp: 'imp-123', imp_exp: 30.minutes.from_now.to_i } }
    let(:view_as_on) { { OS_VIEW_AS: 'on', OS_ACCOUNT_ID: account.id.to_s } }

    def link_query
      Rack::Utils.parse_query(URI.parse(response.parsed_body['url']).query)
    end

    before { create(:platform_app_permissible, platform_app: platform_app, permissible: person) }

    it 'answers 404 with OS_VIEW_AS unset' do
      with_modified_env(OS_VIEW_AS: nil, OS_ACCOUNT_ID: account.id.to_s) { post path, params: body, headers: headers, as: :json }
      expect(response).to have_http_status(:not_found)
    end

    it 'needs the Platform token' do
      with_modified_env(view_as_on) { post path, params: body, as: :json }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'refuses a user the Platform App has no rights over' do
      other = create(:user, account: account)
      with_modified_env(view_as_on) { post "/platform/api/v1/durian_os/users/#{other.id}/view_as_login", params: body, headers: headers, as: :json }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'returns a one-time link with impersonation=true and records the View as against its token' do
      with_modified_env(view_as_on) { post path, params: body, headers: headers, as: :json }

      expect(response).to have_http_status(:success)
      expect(response.parsed_body.keys).to eq(['url'])
      query = link_query
      expect(query['impersonation']).to eq('true')
      expect(query['email']).to eq(person.email)
      expect(person.valid_sso_auth_token?(query['sso_auth_token'])).to be(true)
      record = JSON.parse(Redis::Alfred.get(DurianOs::ViewAs.record_key(person, query['sso_auth_token'])))
      expect(record).to eq('by_email' => 'admin@durian.test', 'by_name' => 'Asha Admin', 'imp' => 'imp-123', 'imp_exp' => body[:imp_exp])
    end

    it 'leaves the plain login link unchanged and unmarked' do
      with_modified_env(view_as_on) { get "/platform/api/v1/users/#{person.id}/login", headers: headers, as: :json }

      expect(response).to have_http_status(:success)
      query = link_query
      expect(query).not_to have_key('impersonation')
      expect(Redis::Alfred.get(DurianOs::ViewAs.record_key(person, query['sso_auth_token']))).to be_nil
    end

    it 'refuses service and emergency users' do
      %w[service emergency].each do |kind|
        protected_user = create(:user, account: account, custom_attributes: { 'os_kind' => kind })
        create(:platform_app_permissible, platform_app: platform_app, permissible: protected_user)
        with_modified_env(view_as_on) do
          post "/platform/api/v1/durian_os/users/#{protected_user.id}/view_as_login", params: body, headers: headers, as: :json
        end
        expect(response).to have_http_status(:forbidden)
        expect(response.parsed_body).to eq('error' => 'not_linkable')
      end
    end

    it 'refuses someone who is not a member of the account' do
      with_modified_env(OS_VIEW_AS: 'on', OS_ACCOUNT_ID: create(:account).id.to_s) { post path, params: body, headers: headers, as: :json }
      expect(response).to have_http_status(:forbidden)
      expect(response.parsed_body).to eq('error' => 'not_a_member')
    end

    it 'refuses a View as that has ended, lacks a field or runs past 65 minutes' do
      {
        { imp_exp: 1.second.ago.to_i } => 'view_as_ended',
        { imp_exp: 2.hours.from_now.to_i } => 'view_as_too_long',
        { imp_exp: 'soon' } => 'invalid_view_as',
        { imp: '' } => 'invalid_view_as',
        { by_email: nil } => 'invalid_view_as'
      }.each do |change, error|
        with_modified_env(view_as_on) { post path, params: body.merge(change), headers: headers, as: :json }
        expect(response).to have_http_status(:unprocessable_entity)
        expect(response.parsed_body).to eq('error' => error)
      end
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
