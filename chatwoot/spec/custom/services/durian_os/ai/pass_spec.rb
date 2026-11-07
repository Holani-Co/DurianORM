require 'rails_helper'
require Rails.root.join('spec/custom/support/durian_os_ai_helpers')

# The Durian OS pass (docs/mcp/MCP.md, section 2): every check and its refusal code.
RSpec.describe DurianOs::Ai::Pass do
  include DurianOsAiHelpers

  let(:sub) { '0B6C2F4E-7D1A-4C55-9E0A-3F1D2C4B5A69' }

  around { |example| with_modified_env(ai_env) { example.run } }

  def refusal(token)
    described_class.verify(token)
    nil
  rescue DurianOs::Ai::Refused => e
    expect(e.status).to eq(401)
    expect(e.message).to eq('This Durian OS connection is not valid. Reconnect in Durian OS.')
    expect(e.headers).to eq('www-authenticate' => 'Bearer')
    e.code
  end

  it 'accepts a good pass and lowercases sub' do
    claims = described_class.verify(ai_pass(sub: sub, max_level: 'edit'))
    expect(claims).to include('sub' => sub.downcase, 'aud' => 'pat:orm', 'typ' => 'pat', 'max_level' => 'edit')
  end

  it 'accepts a header typ of pat or JWT, or none' do
    %w[pat JWT].each { |typ| expect(refusal(ai_pass(sub: sub, header: { typ: typ }))).to be_nil }
    expect(refusal(ai_pass(sub: sub, header: { typ: DurianOsAiHelpers::OMIT }))).to be_nil
  end

  describe 'the switch' do
    it 'refuses every pass while ORM_OS_PASS is unset or off' do
      [nil, 'off', 'no', ''].each do |value|
        with_modified_env('ORM_OS_PASS' => value) do
          expect(refusal(ai_pass(sub: sub))).to eq('not_configured')
          expect(refusal('a.b.c')).to eq('not_configured')
          expect(refusal(nil)).to eq('not_configured')
        end
      end
    end
  end

  describe 'malformed' do
    it 'refuses anything that is not three base64url parts of at most 4 KB' do
      good = ai_pass(sub: sub)
      expect(refusal(nil)).to eq('malformed')
      expect(refusal('')).to eq('malformed')
      expect(refusal('a.b')).to eq('malformed')
      expect(refusal("#{good}.x")).to eq('malformed')
      expect(refusal(good.sub(/\.[^.]+\z/, '.'))).to eq('malformed')
      expect(refusal(good.tr('-', '+'))).to eq('malformed') if good.include?('-')
      expect(refusal("#{good.split('.').first}.#{'A' * 4096}.#{good.split('.').last}")).to eq('malformed')
    end

    it 'refuses a header or payload that is not a JSON object' do
      signature = ai_b64url('x' * 64)
      expect(refusal("#{ai_b64url('not json')}.#{ai_b64url('{}')}.#{signature}")).to eq('malformed')
      expect(refusal("#{ai_b64url('[1]')}.#{ai_b64url('{}')}.#{signature}")).to eq('malformed')

      header = ai_b64url({ alg: 'EdDSA', kid: DurianOsAiHelpers::KID }.to_json)
      payload = ai_b64url('[]')
      input = "#{header}.#{payload}"
      expect(refusal("#{input}.#{ai_b64url(ai_key.sign(nil, input))}")).to eq('malformed')
    end
  end

  describe 'the header' do
    it 'needs alg EdDSA, a kid and no crit' do
      expect(refusal(ai_pass(sub: sub, header: { alg: 'HS256' }))).to eq('bad_signature')
      expect(refusal(ai_pass(sub: sub, header: { alg: 'none' }))).to eq('bad_signature')
      expect(refusal(ai_pass(sub: sub, header: { kid: DurianOsAiHelpers::OMIT }))).to eq('bad_signature')
      expect(refusal(ai_pass(sub: sub, header: { kid: '' }))).to eq('bad_signature')
      expect(refusal(ai_pass(sub: sub, header: { kid: 7 }))).to eq('bad_signature')
      expect(refusal(ai_pass(sub: sub, header: { crit: ['exp'] }))).to eq('bad_signature')
    end

    it 'refuses a header typ naming another kind of token' do
      expect(refusal(ai_pass(sub: sub, header: { typ: 'at+jwt' }))).to eq('wrong_type')
      expect(refusal(ai_pass(sub: sub, header: { typ: 1 }))).to eq('wrong_type')
    end
  end

  describe 'the key' do
    it 'refuses a kid that is not in the pinned set' do
      expect(refusal(ai_pass(sub: sub, header: { kid: 'another-key' }))).to eq('unknown_key')
    end

    it 'refuses every pass when the set has no usable key' do
      with_modified_env('OS_JWKS_JSON' => nil) { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }
      with_modified_env('OS_JWKS_JSON' => 'not json') { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }
      with_modified_env('OS_JWKS_JSON' => '{"keys":[]}') { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }

      rsa = { keys: [{ kty: 'RSA', kid: DurianOsAiHelpers::KID, n: 'AQAB', e: 'AQAB' }] }.to_json
      with_modified_env('OS_JWKS_JSON' => rsa) { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }

      short = { keys: [{ kty: 'OKP', crv: 'Ed25519', kid: DurianOsAiHelpers::KID, x: ai_b64url('x' * 31) }] }.to_json
      with_modified_env('OS_JWKS_JSON' => short) { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }
    end

    it 'refuses a kid listed twice' do
      twice = { keys: JSON.parse(ai_jwks)['keys'] * 2 }.to_json
      with_modified_env('OS_JWKS_JSON' => twice) { expect(refusal(ai_pass(sub: sub))).to eq('unknown_key') }
    end

    it 'takes any key of the set by its kid' do
      other = OpenSSL::PKey.generate_key('ED25519')
      both = { keys: JSON.parse(ai_jwks)['keys'] + JSON.parse(ai_jwks(other, kid: 'next-key'))['keys'] }.to_json
      with_modified_env('OS_JWKS_JSON' => both) do
        expect(refusal(ai_pass(sub: sub, key: other, header: { kid: 'next-key' }))).to be_nil
        expect(refusal(ai_pass(sub: sub, key: other))).to eq('bad_signature')
      end
    end
  end

  describe 'the signature' do
    it 'refuses a pass signed by another key or changed after signing' do
      expect(refusal(ai_pass(sub: sub, key: OpenSSL::PKey.generate_key('ED25519')))).to eq('bad_signature')

      header, payload, signature = ai_pass(sub: sub, max_level: 'view').split('.')
      changed = ai_b64url(JSON.parse(Base64.urlsafe_decode64(payload)).merge('max_level' => 'administer').to_json)
      expect(refusal("#{header}.#{changed}.#{signature}")).to eq('bad_signature')
      expect(refusal("#{header}.#{payload}.#{ai_b64url('x' * 64)}")).to eq('bad_signature')
    end
  end

  describe 'the claims' do
    it 'needs Durian OS as the issuer' do
      expect(refusal(ai_pass(sub: sub, claims: { iss: 'https://evil.test' }))).to eq('wrong_issuer')
      expect(refusal(ai_pass(sub: sub, claims: { iss: DurianOsAiHelpers::OMIT }))).to eq('wrong_issuer')
      with_modified_env('OS_ISSUER' => 'https://os.test') do
        expect(refusal(ai_pass(sub: sub, claims: { iss: 'https://os.test' }))).to be_nil
        expect(refusal(ai_pass(sub: sub))).to eq('wrong_issuer')
      end
    end

    it 'needs aud to be exactly pat:orm, never a list' do
      expect(refusal(ai_pass(sub: sub, claims: { aud: 'pat:studio' }))).to eq('wrong_audience')
      expect(refusal(ai_pass(sub: sub, claims: { aud: ['pat:orm'] }))).to eq('wrong_audience')
      expect(refusal(ai_pass(sub: sub, claims: { aud: %w[pat:orm pat:studio] }))).to eq('wrong_audience')
      expect(refusal(ai_pass(sub: sub, claims: { aud: DurianOsAiHelpers::OMIT }))).to eq('wrong_audience')
    end

    it 'needs typ pat: a hand-off or a request token is never a pass' do
      expect(refusal(ai_pass(sub: sub, claims: { typ: 'handoff' }))).to eq('wrong_type')
      expect(refusal(ai_pass(sub: sub, claims: { typ: DurianOsAiHelpers::OMIT }))).to eq('wrong_type')
    end

    it 'keeps the clock window: at most 60 seconds of life and 30 of skew' do
      now = Time.now.to_i
      expect(refusal(ai_pass(sub: sub, claims: { iat: now, exp: now + 61 }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { iat: now, exp: now }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { iat: now - 100, exp: now - 40 }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { iat: now - 85, exp: now - 25 }))).to be_nil
      expect(refusal(ai_pass(sub: sub, claims: { iat: now + 35, exp: now + 60 }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { iat: now + 20, exp: now + 60 }))).to be_nil
      expect(refusal(ai_pass(sub: sub, claims: { nbf: now + 40 }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { nbf: now }))).to be_nil
      expect(refusal(ai_pass(sub: sub, claims: { iat: now.to_f, exp: now + 60 }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { exp: DurianOsAiHelpers::OMIT }))).to eq('expired')
      expect(refusal(ai_pass(sub: sub, claims: { iat: true }))).to eq('expired')
    end

    it 'refuses View as claims: a View as is the browser' do
      %w[impersonated_by imp imp_exp act].each do |name|
        expect(refusal(ai_pass(sub: sub, claims: { name => 'x' }))).to eq('view_as')
      end
    end

    it 'needs a well-formed jti and a UUID sub' do
      expect(refusal(ai_pass(sub: sub, claims: { jti: 'short' }))).to eq('bad_jti')
      expect(refusal(ai_pass(sub: sub, claims: { jti: "#{'a' * 22}!" }))).to eq('bad_jti')
      expect(refusal(ai_pass(sub: sub, claims: { jti: DurianOsAiHelpers::OMIT }))).to eq('bad_jti')
      expect(refusal(ai_pass(sub: 'tara.fern@orm.test'))).to eq('unknown_person')
      expect(refusal(ai_pass(sub: 42))).to eq('unknown_person')
    end
  end
end
