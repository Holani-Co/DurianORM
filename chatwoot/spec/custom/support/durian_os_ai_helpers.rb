# Made-up Durian OS passes for the AI connection specs (docs/mcp/MCP.md, section 14): a throwaway Ed25519 key, its
# pinned key set, and passes signed with it the way Durian OS's front door mints them.
module DurianOsAiHelpers
  KID = 'orm-test-key'.freeze
  OMIT = :omit

  def ai_key
    @ai_key ||= OpenSSL::PKey.generate_key('ED25519')
  end

  def ai_jwks(*keys, kid: KID)
    keys = [ai_key] if keys.empty?
    { keys: keys.map { |key| { kty: 'OKP', crv: 'Ed25519', x: ai_b64url(key.raw_public_key), kid: kid, alg: 'EdDSA', use: 'sig' } } }.to_json
  end

  # The switch on and the key set pinned, as on the box after the release.
  def ai_env(extra = {})
    { 'ORM_OS_PASS' => 'on', 'OS_JWKS_JSON' => ai_jwks }.merge(extra.transform_keys(&:to_s))
  end

  def ai_pass(sub:, max_level: 'administer', client: 'Claude', key: nil, header: {}, claims: {}, now: Time.now.to_i)
    head = { alg: 'EdDSA', kid: KID }.merge(header).reject { |_, value| value == OMIT }
    body = {
      iss: 'https://durianos.in', aud: 'pat:orm', typ: 'pat', sub: sub, email: 'someone@orm.test', name: 'Someone',
      groups: [], max_level: max_level, client: client, iat: now, exp: now + 60, jti: SecureRandom.urlsafe_base64(16)
    }.merge(claims).reject { |_, value| value == OMIT }
    input = "#{ai_b64url(head.to_json)}.#{ai_b64url(body.to_json)}"
    "#{input}.#{ai_b64url((key || ai_key).sign(nil, input))}"
  end

  def ai_jti(pass)
    JSON.parse(Base64.urlsafe_decode64(pass.split('.')[1]))['jti']
  end

  def ai_headers(pass)
    { 'Authorization' => "Bearer #{pass}" }
  end

  def ai_b64url(data)
    Base64.urlsafe_encode64(data, padding: false)
  end
end
