# A Durian OS pass (docs/mcp/MCP.md, section 2): an EdDSA (Ed25519) JWT Durian OS mints for one call to ORM,
# aud `pat:orm`, typ `pat`, 60 seconds of life. `verify` returns its claims (sub lowercased) or raises
# DurianOs::Ai::Refused with 401 and the same words for every reason; the reason goes only to the log line.
#
# No new gem: Ruby's OpenSSL verifies Ed25519 against the keys pinned in OS_JWKS_JSON. Rails never fetches keys,
# and a set with no usable key refuses every pass. The jti (one use) and the person are checked by the door.
module DurianOs::Ai::Pass
  AUDIENCE = 'pat:orm'.freeze
  MAX_BYTES = 4096
  MAX_LIFETIME = 60
  CLOCK_SKEW = 30
  # A pass is always the person's own: one that names a View as, or acting for someone else, is refused.
  NOT_ONES_OWN = %w[impersonated_by imp imp_exp act].freeze
  SEGMENT = /\A[A-Za-z0-9_-]+\z/
  # Durian OS's jti: 16 random bytes, base64url (22 characters); up to 64 allowed.
  JTI = /\A[A-Za-z0-9_-]{22,64}\z/
  PERSON_ID = /\A\h{8}-\h{4}-\h{4}-\h{4}-\h{12}\z/
  HEADER_TYPES = %w[pat jwt].freeze
  # An Ed25519 SubjectPublicKeyInfo is this DER prefix followed by the 32 bytes of the key.
  ED25519_DER_PREFIX = ['302a300506032b6570032100'].pack('H*').freeze

  module_function

  def verify(token, now: Time.now.to_i)
    refuse!('not_configured') unless DurianOs::Config.os_pass?

    header_part, payload_part, signature_part = split(token)
    header = json_part(header_part)
    check_header!(header)
    key = keys[header['kid']] || refuse!('unknown_key')
    refuse!('bad_signature') unless signed?(key, signature_part, "#{header_part}.#{payload_part}")

    claims = json_part(payload_part)
    check_claims!(claims, now)
    claims.merge('sub' => claims['sub'].downcase)
  end

  def split(token)
    refuse!('malformed') unless token.is_a?(String) && token.bytesize <= MAX_BYTES

    parts = token.split('.', -1)
    refuse!('malformed') unless parts.size == 3 && parts.all? { |part| SEGMENT.match?(part) }
    parts
  end

  def json_part(part)
    value = JSON.parse(base64url(part))
    value.is_a?(Hash) ? value : refuse!('malformed')
  rescue ArgumentError, JSON::ParserError, EncodingError
    refuse!('malformed')
  end

  def check_header!(header)
    kid = header['kid']
    refuse!('bad_signature') unless header['alg'] == 'EdDSA' && kid.is_a?(String) && kid.present? && !header.key?('crit')

    typ = header['typ']
    refuse!('wrong_type') unless typ.nil? || (typ.is_a?(String) && HEADER_TYPES.include?(typ.downcase))
  end

  def signed?(key, signature_part, input)
    key.verify(nil, base64url(signature_part), input)
  rescue ArgumentError, OpenSSL::PKey::PKeyError
    false
  end

  def check_claims!(claims, now)
    refuse!('wrong_issuer') unless claims['iss'] == DurianOs::Config.os_issuer
    # One app per pass: aud is the one string, never a list naming several.
    refuse!('wrong_audience') unless claims['aud'] == AUDIENCE
    refuse!('wrong_type') unless claims['typ'] == 'pat'
    refuse!('expired') unless in_window?(claims, now)
    refuse!('view_as') if NOT_ONES_OWN.any? { |name| claims.key?(name) }
    refuse!('bad_jti') unless claims['jti'].is_a?(String) && JTI.match?(claims['jti'])
    refuse!('unknown_person') unless claims['sub'].is_a?(String) && PERSON_ID.match?(claims['sub'])
  end

  # At most 60 seconds of life, 30 seconds of clock skew either side, as the other Durian apps.
  def in_window?(claims, now)
    iat = claims['iat']
    exp = claims['exp']
    nbf = claims['nbf']
    return false unless [iat, exp].all?(Integer)
    return false unless (exp - iat).positive? && exp - iat <= MAX_LIFETIME
    return false unless now.between?(iat - CLOCK_SKEW, exp + CLOCK_SKEW)

    nbf.nil? || (nbf.is_a?(Integer) && nbf <= now + CLOCK_SKEW)
  end

  # The pinned key set, by kid: OKP Ed25519 signing keys only. A kid listed twice is dropped. Parsed again only
  # when OS_JWKS_JSON changes.
  def keys
    raw = DurianOs::Config.os_jwks_json
    cached = @keys
    return cached.last if cached && cached.first == raw

    parsed = parse_keys(raw)
    @keys = [raw, parsed]
    parsed
  end

  def parse_keys(raw)
    list = raw && JSON.parse(raw)
    list = list['keys'] if list.is_a?(Hash)
    return {}.freeze unless list.is_a?(Array)

    kids = list.filter_map { |jwk| jwk['kid'] if jwk.is_a?(Hash) }.tally
    list.each_with_object({}) do |jwk, found|
      key = ed25519_key(jwk)
      found[jwk['kid']] = key if key && kids[jwk['kid']] == 1
    end.freeze
  rescue JSON::ParserError
    {}.freeze
  end

  def ed25519_key(jwk)
    return unless jwk.is_a?(Hash) && jwk['kty'] == 'OKP' && jwk['crv'] == 'Ed25519'
    return unless jwk['kid'].is_a?(String) && jwk['kid'].present? && jwk['x'].is_a?(String)
    return if (jwk.key?('alg') && jwk['alg'] != 'EdDSA') || (jwk.key?('use') && jwk['use'] != 'sig')

    raw = base64url(jwk['x'])
    OpenSSL::PKey.read(ED25519_DER_PREFIX + raw) if raw.bytesize == 32
  rescue ArgumentError, OpenSSL::PKey::PKeyError
    nil
  end

  def base64url(text)
    Base64.urlsafe_decode64(text)
  end

  def refuse!(code)
    DurianOs::Ai.refuse!(401, code, DurianOs::Ai::PASS_INVALID, headers: { 'www-authenticate' => 'Bearer' })
  end
end
