require 'rails_helper'

# The other ways in that OS_SIGNIN_ONLY turns off (ORM appendix code changes 3-7).
RSpec.describe 'Durian OS sign-in switch', type: :request do
  let(:account) { create(:account) }
  let(:user) { create(:user, account: account, password: 'Test@123456') }
  let(:handoff) { 'https://durianos.test/api/handoff/orm' }

  describe 'password reset' do
    it 'works as today with the switch unset' do
      post '/auth/password', params: { email: user.email }
      expect(response).to have_http_status(:ok)
    end

    it 'answers 404 with the switch on' do
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        post '/auth/password', params: { email: user.email }
        expect(response).to have_http_status(:not_found)

        token = user.send(:set_reset_password_token)
        put '/auth/password', params: { reset_password_token: token, password: 'New@123456', password_confirmation: 'New@123456' }
        expect(response).to have_http_status(:not_found)
        expect(response.headers['access-token']).to be_nil
      end
    end
  end

  describe 'confirmation' do
    it 'confirms without signing in and points at Durian OS' do
      unconfirmed = create(:user, skip_confirmation: false)
      unconfirmed.update!(confirmed_at: nil)
      unconfirmed.send(:generate_confirmation_token!)
      token = unconfirmed.reload.confirmation_token

      with_modified_env OS_SIGNIN_ONLY: 'on', OS_HANDOFF_URL: handoff do
        post '/auth/confirmation', params: { confirmation_token: token }
      end
      expect(response).to have_http_status(:ok)
      expect(response.parsed_body['redirect_url']).to eq(handoff)
      expect(response.headers['access-token']).to be_nil
      expect(unconfirmed.reload.confirmed?).to be(true)
    end
  end

  describe '/app/login' do
    it 'renders as today with the switch unset' do
      get '/app/login'
      expect(response).to have_http_status(:success)
    end

    it 'redirects to Durian OS, once a minute at most' do
      with_modified_env OS_SIGNIN_ONLY: 'on', OS_HANDOFF_URL: handoff do
        get '/app/login'
        expect(response).to redirect_to(handoff)

        get '/app/login'
        expect(response).to have_http_status(:success)
      end
    end

    it 'keeps the one-time token page, uncached and without a referrer' do
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        get '/app/login', params: { email: user.email, sso_auth_token: 'abc' }
      end
      expect(response).to have_http_status(:success)
      expect(response.headers['Cache-Control']).to include('no-store')
      expect(response.headers['Referrer-Policy']).to eq('no-referrer')
    end

    it 'shows the form for ?emergency=1 only while the emergency login is on' do
      emergency = { OS_SIGNIN_ONLY: 'on', OS_HANDOFF_URL: handoff, OS_EMERGENCY_EMAIL: 'emergency@orm.test',
                    OS_EMERGENCY_PASSWORD_HASH: BCrypt::Password.create('x' * 24, cost: 12).to_s }
      with_modified_env(emergency) do
        get '/app/login', params: { emergency: '1' }
        expect(response).to redirect_to(handoff)
      end
      cookies.delete('durian_os_login_redirect')
      with_modified_env(emergency.merge(OS_EMERGENCY_LOGIN_UNTIL: 1.hour.from_now.utc.iso8601)) do
        get '/app/login', params: { emergency: '1' }
        expect(response).to have_http_status(:success)
      end
    end
  end

  describe 'super admin console' do
    let(:super_admin) { create(:super_admin, password: 'Super@123456') }

    it 'signs in as today with the switch unset' do
      post '/super_admin/sign_in', params: { super_admin: { email: super_admin.email, password: 'Super@123456' } }
      expect(response).to redirect_to('/super_admin/users')
    end

    it 'answers 404 with the switch on and the emergency login off' do
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        get '/super_admin/sign_in'
        expect(response).to have_http_status(:not_found)
        post '/super_admin/sign_in', params: { super_admin: { email: super_admin.email, password: 'Super@123456' } }
        expect(response).to have_http_status(:not_found)
      end
    end

    it 'never accepts a SuperAdmin database password, even with the emergency login on' do
      emergency = { OS_SIGNIN_ONLY: 'on', OS_EMERGENCY_EMAIL: 'emergency@orm.test', OS_EMERGENCY_LOGIN_UNTIL: 1.hour.from_now.utc.iso8601,
                    OS_EMERGENCY_PASSWORD_HASH: BCrypt::Password.create('emergency-password-0123456789', cost: 12).to_s }
      with_modified_env(emergency) do
        post '/super_admin/sign_in', params: { super_admin: { email: super_admin.email, password: 'Super@123456' } }
        expect(response).to redirect_to('/super_admin/sign_in')

        emergency_admin = create(:super_admin, email: 'emergency@orm.test', custom_attributes: { 'os_kind' => 'emergency' })
        post '/super_admin/sign_in', params: { super_admin: { email: emergency_admin.email, password: 'emergency-password-0123456789' } }
        expect(response).to redirect_to('/super_admin/users')
        expect(emergency_admin.audits.where(action: 'emergency_super_admin_sign_in')).to exist
        get '/super_admin/users'
        expect(response).to have_http_status(:success)
      end
    end

    it 'ends a console session that the emergency login did not start' do
      sign_in(super_admin, scope: :super_admin)
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        get '/super_admin/users'
        expect(response).to have_http_status(:not_found)
      end
    end
  end

  describe 'Google and SAML' do
    it 'sends the person to Durian OS with the switch on' do
      controller = DeviseOverrides::OmniauthCallbacksController.new
      allow(controller).to receive(:redirect_to)
      with_modified_env OS_SIGNIN_ONLY: 'on', OS_HANDOFF_URL: handoff do
        controller.send(:sign_in_user)
        controller.send(:sign_up_user)
        controller.send(:sign_in_user_on_mobile)
      end
      expect(controller).to have_received(:redirect_to).with(handoff, allow_other_host: true).exactly(3).times
    end
  end

  describe 'sign-up through the registrations route' do
    it 'is not routed with the switch on' do
      with_modified_env OS_SIGNIN_ONLY: 'on' do
        Rails.application.reload_routes!
        post '/auth', params: { email: 'new@orm.test', password: 'New@123456', password_confirmation: 'New@123456' }
        expect(response).to have_http_status(:not_found)
      end
      Rails.application.reload_routes!
      expect(User.from_email('new@orm.test')).to be_nil
    end
  end
end
