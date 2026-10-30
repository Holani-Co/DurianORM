require 'rails_helper'

# A Durian OS "View as" session in use (shared View as spec 2, 3, 5 and 6).
RSpec.describe 'Durian OS View as session', type: :request do
  let(:account) { create(:account) }
  let(:user) { create(:user, account: account, role: :administrator) }
  let(:ends_at) { 30.minutes.from_now.change(usec: 0) }
  let(:blocked) { { 'error' => 'Not available while viewing as someone.' } }

  around { |example| with_modified_env(OS_VIEW_AS: 'on') { example.run } }

  def view_as_headers(person = user)
    session = DurianOs::ViewAs::Session.new(by_email: 'admin@durian.test', by_name: 'Asha Admin', imp: 'imp-123', imp_exp: ends_at.to_i)
    token = person.create_token(lifespan: DurianOs::ViewAs.lifespan(session), os_view_as: session.to_record)
    person.save!
    person.build_auth_headers(token.token, token.client)
  end

  describe 'blocked while viewing as someone' do
    it 'refuses a password change' do
      password = { current_password: 'Password1!', password: 'Changed@12345', password_confirmation: 'Changed@12345' }
      put '/api/v1/profile', params: { profile: password }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
      expect(response.parsed_body).to eq(blocked)
      expect(user.reload.valid_password?('Password1!')).to be(true)
    end

    it 'refuses profile and settings writes' do
      put '/api/v1/profile', params: { profile: { name: 'Someone Else', ui_settings: { locale: 'fr' } } }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
      expect(user.reload.name).not_to eq('Someone Else')

      delete '/api/v1/profile/avatar', headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
    end

    it 'refuses an API token reset' do
      old_token = user.access_token.token
      post '/api/v1/profile/reset_access_token', headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
      expect(user.access_token.reload.token).to eq(old_token)
    end

    it 'refuses availability changes' do
      post '/api/v1/profile/availability', params: { profile: { account_id: account.id, availability: 'busy' } }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
      expect(user.account_users.first.reload.availability).not_to eq('busy')

      post '/api/v1/profile/auto_offline', params: { profile: { account_id: account.id, auto_offline: false } }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
    end

    it 'refuses MFA, push subscriptions, password reset and /auth account changes' do
      post '/api/v1/profile/mfa', headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)

      post '/api/v1/notification_subscriptions', params: { notification_subscription: { subscription_type: 'browser_push' } },
                                                 headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)

      post '/auth/password', params: { email: user.email }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)

      put '/auth', params: { password: 'Changed@12345', password_confirmation: 'Changed@12345' }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:forbidden)
      expect(response.parsed_body).to eq(blocked)
      expect(user.reload.valid_password?('Password1!')).to be(true)
    end

    it 'still lets the person do everything else' do
      get '/api/v1/profile', headers: view_as_headers, as: :json
      expect(response).to have_http_status(:success)

      post "/api/v1/accounts/#{account.id}/teams", params: { name: 'view-as-team' }, headers: view_as_headers, as: :json
      expect(response).to have_http_status(:success)
    end

    it 'blocks nothing in a normal session' do
      post '/api/v1/profile/reset_access_token', headers: user.create_new_auth_token, as: :json
      expect(response).to have_http_status(:success)
    end
  end

  describe 'the current user' do
    it 'carries the admin and the end, and no API token' do
      get '/auth/validate_token', headers: view_as_headers
      data = response.parsed_body['payload']['data']
      expect(data['impersonated_by']).to eq('email' => 'admin@durian.test', 'name' => 'Asha Admin')
      expect(Time.zone.parse(data['view_as_ends_at']).to_i).to eq(ends_at.to_i)
      expect(data['access_token']).to be_nil

      get '/api/v1/profile', headers: view_as_headers, as: :json
      expect(response.parsed_body['impersonated_by']).to eq('email' => 'admin@durian.test', 'name' => 'Asha Admin')
      expect(response.parsed_body['access_token']).to be_nil
    end

    it 'is unmarked in a normal session' do
      get '/api/v1/profile', headers: user.create_new_auth_token, as: :json
      expect(response.parsed_body['impersonated_by']).to be_nil
      expect(response.parsed_body['view_as_ends_at']).to be_nil
      expect(response.parsed_body['access_token']).to eq(user.access_token.token)
    end
  end

  describe 'the session' do
    it 'ends at imp_exp' do
      headers = view_as_headers
      travel_to(ends_at - 1.minute) do
        get '/api/v1/profile', headers: headers, as: :json
        expect(response).to have_http_status(:success)
      end
      travel_to(ends_at + 1.second) do
        get '/api/v1/profile', headers: headers, as: :json
        expect(response).to have_http_status(:unauthorized)
      end
    end

    it 'ends as soon as OS_VIEW_AS goes off' do
      headers = view_as_headers
      with_modified_env(OS_VIEW_AS: nil) { get '/api/v1/profile', headers: headers, as: :json }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'keeps the admin and the end when the token is renewed' do
      client = view_as_headers['client']
      user.reload.create_new_auth_token(client)

      entry = user.reload.tokens[client]
      expect(entry['os_view_as']).to include('by_email' => 'admin@durian.test', 'imp_exp' => ends_at.to_i)
      expect(entry['expiry']).to be <= ends_at.to_i
    end

    it 'never outlives the normal lifetime' do
      session = DurianOs::ViewAs::Session.new(by_email: 'a@b.c', imp: 'x', imp_exp: 1.year.from_now.to_i)
      expect(DurianOs::ViewAs.lifespan(session)).to be <= DeviseTokenAuth.token_lifespan
    end
  end

  describe 'audit rows written while viewing' do
    it 'name the admin' do
      post "/api/v1/accounts/#{account.id}/teams", params: { name: 'view-as-audit' }, headers: view_as_headers, as: :json

      audit = Enterprise::AuditLog.where(auditable_type: 'Team').order(:id).last
      expect(audit.comment).to eq("Asha Admin <admin@durian.test> viewing as #{user.email} (imp imp-123)")
    end

    it 'are unmarked in a normal session' do
      post "/api/v1/accounts/#{account.id}/teams", params: { name: 'normal-audit' }, headers: user.create_new_auth_token, as: :json

      expect(Enterprise::AuditLog.where(auditable_type: 'Team').order(:id).last.comment).to be_nil
    end
  end
end
