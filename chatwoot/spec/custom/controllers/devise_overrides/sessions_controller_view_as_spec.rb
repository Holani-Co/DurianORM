require 'rails_helper'

# Using a Durian OS "View as" link (shared View as spec 1-3, 6).
RSpec.describe DeviseOverrides::SessionsController, type: :controller do
  include Devise::Test::ControllerHelpers

  render_views

  let(:account) { create(:account) }
  let(:user) { create(:user, account: account) }
  let(:imp_exp) { 30.minutes.from_now.to_i }
  let(:view_as) do
    DurianOs::ViewAs::Session.new(by_email: 'admin@durian.test', by_name: 'Asha Admin', imp: 'imp-123', imp_exp: imp_exp)
  end
  let(:link_token) do
    url = with_modified_env(OS_VIEW_AS: 'on') { DurianOs::ViewAs.create_link!(user, view_as) }
    Rack::Utils.parse_query(URI.parse(url).query)['sso_auth_token']
  end

  before { request.env['devise.mapping'] = Devise.mappings[:user] }

  def session_entries
    user.reload.tokens.values
  end

  context 'with OS_VIEW_AS on' do
    around { |example| with_modified_env(OS_VIEW_AS: 'on') { example.run } }

    it 'signs in as the person with a session marked with the admin that ends at imp_exp' do
      post :create, params: { email: user.email, sso_auth_token: link_token }

      expect(response).to have_http_status(:success)
      entry = session_entries.last
      expect(entry['os_view_as']).to eq('by_email' => 'admin@durian.test', 'by_name' => 'Asha Admin', 'imp' => 'imp-123', 'imp_exp' => imp_exp)
      expect(entry['expiry']).to be <= imp_exp
      expect(response.headers['expiry'].to_i).to be <= imp_exp

      data = response.parsed_body['data']
      expect(data['impersonated_by']).to eq('email' => 'admin@durian.test', 'name' => 'Asha Admin')
      expect(Time.zone.parse(data['view_as_ends_at']).to_i).to eq(imp_exp)
      expect(data['access_token']).to be_nil
    end

    it 'writes the sign-in audit row naming both people' do
      post :create, params: { email: user.email, sso_auth_token: link_token }

      audit = user.audits.where(action: 'sign_in').order(:id).last
      expect(audit.comment).to eq("Asha Admin <admin@durian.test> started viewing as #{user.email} (imp imp-123)")
      expect(DurianOs::Current.view_as).to be_nil
    end

    it 'works once' do
      token = link_token
      post :create, params: { email: user.email, sso_auth_token: token }
      expect(response).to have_http_status(:success)

      post :create, params: { email: user.email, sso_auth_token: token }
      expect(response).to have_http_status(:unauthorized)
    end

    it 'refuses the link once the View as has ended' do
      token = link_token
      travel_to(Time.zone.at(imp_exp) + 1.second) do
        post :create, params: { email: user.email, sso_auth_token: token }
      end
      expect(response).to have_http_status(:unauthorized)
      expect(session_entries).to be_empty
    end

    it 'leaves a normal one-time link unmarked' do
      post :create, params: { email: user.email, sso_auth_token: user.generate_sso_auth_token }

      expect(response).to have_http_status(:success)
      expect(session_entries.last).not_to have_key('os_view_as')
      expect(response.parsed_body['data']['impersonated_by']).to be_nil
      expect(response.parsed_body['data']['access_token']).to eq(user.access_token.token)
    end
  end

  it 'refuses a View as link, never signing in unmarked, when OS_VIEW_AS went off before it was used' do
    token = link_token
    with_modified_env(OS_VIEW_AS: nil) { post :create, params: { email: user.email, sso_auth_token: token } }

    expect(response).to have_http_status(:unauthorized)
    expect(session_entries).to be_empty
  end
end
