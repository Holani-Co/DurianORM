require 'rails_helper'

RSpec.describe Rack::Attack do
  def sign_in_request(params)
    env = Rack::MockRequest.env_for('/auth/sign_in', method: 'POST', params: params)
    Rack::Attack::Request.new(env)
  end

  let(:handoff) { sign_in_request(email: 'a@orm.test', sso_auth_token: 'x' * 64) }
  let(:password) { sign_in_request(email: 'a@orm.test', password: 'guess') }

  it 'keeps throttling every sign-in post with OS_SIGNIN_ONLY unset' do
    expect(described_class.durian_os_handoff_post?(handoff)).to be(false)
  end

  it 'skips the login throttles only for one-time token posts with OS_SIGNIN_ONLY on' do
    with_modified_env OS_SIGNIN_ONLY: 'on' do
      expect(described_class.durian_os_handoff_post?(handoff)).to be(true)
      expect(described_class.durian_os_handoff_post?(password)).to be(false)
      expect(described_class.durian_os_handoff_post?(sign_in_request(sso_auth_token: 'x', password: 'guess'))).to be(false)
    end
  end
end
