require 'rails_helper'

RSpec.describe DeviseOverrides::SessionsController, type: :controller do
  include Devise::Test::ControllerHelpers
  include ActiveJob::TestHelper

  let(:account) { create(:account) }
  let(:user) { create(:user, account: account, password: 'Test@123456') }
  let(:emergency_user) do
    create(:user, account: account, role: :administrator, email: 'emergency@orm.test',
                  custom_attributes: { 'os_kind' => 'emergency' })
  end
  let(:emergency_password) { 'emergency-password-0123456789' }
  let(:emergency_hash) { BCrypt::Password.create(emergency_password, cost: 12).to_s }
  let(:emergency_off) { { OS_EMERGENCY_EMAIL: 'emergency@orm.test', OS_EMERGENCY_PASSWORD_HASH: emergency_hash, OS_EMERGENCY_LOGIN_UNTIL: nil } }
  let(:emergency_on) { emergency_off.merge(OS_EMERGENCY_LOGIN_UNTIL: 2.hours.from_now.utc.iso8601) }

  before do
    request.env['devise.mapping'] = Devise.mappings[:user]
    request.env['REMOTE_ADDR'] = "10.0.0.#{rand(1..250)}"
    $alfred.with { |conn| conn.scan_each(match: "#{DurianOs::EmergencyLogin::KEY_PREFIX}*") { |key| conn.del(key) } } # rubocop:disable Style/GlobalVars
  end

  context 'with OS_SIGNIN_ONLY unset (today)' do
    it 'still signs in with a password' do
      with_modified_env(OS_SIGNIN_ONLY: nil) { post :create, params: { email: user.email, password: 'Test@123456' } }
      expect(response).to have_http_status(:success)
    end
  end

  context 'with OS_SIGNIN_ONLY on' do
    around { |example| with_modified_env(OS_SIGNIN_ONLY: 'on') { example.run } }

    it 'answers 404 to the password form' do
      post :create, params: { email: user.email, password: 'Test@123456' }
      expect(response).to have_http_status(:not_found)
      expect(response.headers['access-token']).to be_nil
    end

    it 'answers 404 to an MFA verification post' do
      post :create, params: { mfa_token: 'anything', otp_code: '000000' }
      expect(response).to have_http_status(:not_found)
    end

    it 'signs in with the one-time token from Durian OS exactly once' do
      token = user.generate_sso_auth_token

      post :create, params: { email: user.email, sso_auth_token: token }
      expect(response).to have_http_status(:success)

      post :create, params: { email: user.email, sso_auth_token: token }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'refuses a bad token even with a correct password' do
      post :create, params: { email: user.email, sso_auth_token: 'invalid', password: 'Test@123456' }
      expect(response).to have_http_status(:unauthorized)
      expect(response.headers['access-token']).to be_nil
    end

    it 'refuses the emergency login while it is off (404)' do
      emergency_user
      with_modified_env(emergency_off) { post :create, params: { email: 'emergency@orm.test', password: emergency_password } }
      expect(response).to have_http_status(:not_found)
    end

    context 'with the emergency login on' do
      around { |example| with_modified_env(emergency_on) { example.run } }

      before { emergency_user }

      it 'signs the emergency account in against the hash, for one hour, and reports it' do
        with_modified_env OS_REPORT_URL: 'https://durianos.test/api/os/v2/changes',
                          OS_REPORT_KEY: 'AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8', OS_REPORT_KEY_ID: 'orm-2026-10' do
          clear_enqueued_jobs
          post :create, params: { email: 'EMERGENCY@orm.test', password: emergency_password }
          expect(response).to have_http_status(:success)
          kinds = enqueued_jobs.select { |job| job[:job] == DurianOs::ChangeReportJob }
                               .flat_map { |job| JSON.parse(job[:args].first)['changes'].pluck('kind') }
          expect(kinds).to include('emergency_login.used')
        end
        expect(emergency_user.audits.where(action: 'sign_in')).to exist
        expiry = emergency_user.reload.tokens.values.map { |t| t['expiry'] }.max
        expect(expiry).to be <= 1.hour.from_now.to_i + 5
      end

      it 'never accepts the database password' do
        emergency_user.update!(password: 'Db@Password123')
        post :create, params: { email: 'emergency@orm.test', password: 'Db@Password123' }
        expect(response).to have_http_status(:unauthorized)
      end

      it 'locks after 5 failures from one address' do
        5.times { post :create, params: { email: 'emergency@orm.test', password: 'wrong' } }
        post :create, params: { email: 'emergency@orm.test', password: emergency_password }
        expect(response).to have_http_status(:unauthorized)
      end

      it 'still refuses everyone else (404)' do
        post :create, params: { email: user.email, password: 'Test@123456' }
        expect(response).to have_http_status(:not_found)
      end

      it 'ends the emergency session as soon as the login goes off' do
        post :create, params: { email: 'emergency@orm.test', password: emergency_password }
        token = response.headers['access-token']
        client = response.headers['client']
        expect(emergency_user.reload.valid_token?(token, client)).to be(true)
        with_modified_env(OS_EMERGENCY_LOGIN_UNTIL: 1.minute.ago.utc.iso8601) do
          expect(emergency_user.reload.valid_token?(token, client)).to be(false)
        end
      end
    end

    it 'never signs the emergency account in with a one-time token' do
      with_modified_env(emergency_on) do
        token = emergency_user.generate_sso_auth_token
        post :create, params: { email: emergency_user.email, sso_auth_token: token }
      end
      expect(response).to have_http_status(:unauthorized)
    end
  end

  it 'consumes the one-time token with a single DEL even with OS_SIGNIN_ONLY unset' do
    token = user.generate_sso_auth_token
    post :create, params: { email: user.email, sso_auth_token: token }
    expect(response).to have_http_status(:success)
    expect(user.valid_sso_auth_token?(token)).to be(false)
  end
end
