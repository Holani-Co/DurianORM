# Durian OS settings for ORM, all read from the environment.
# Every switch is off when unset (contract 10.8, code change 14).
module DurianOs::Config
  APP_KEY = 'orm'.freeze
  ON_VALUES = %w[on true 1 yes].freeze
  EMERGENCY_MAX_AHEAD = 24.hours
  MIN_BCRYPT_COST = 12

  class InvalidReportKey < StandardError; end

  module_function

  # Makes Durian OS the only way in: password, Google/SAML, reset, sign-up and
  # the super admin console are refused (code changes 2-8 and 12).
  def signin_only?
    switch_on?('OS_SIGNIN_ONLY')
  end

  # ORM is managed by Durian OS: removing an agent keeps the user row and
  # service/emergency rows are protected (code change 9).
  def managed?
    switch_on?('OS_MANAGED')
  end

  def account_id
    ENV.fetch('OS_ACCOUNT_ID', '1').to_i
  end

  def handoff_url
    ENV.fetch('OS_HANDOFF_URL', 'https://durianos.in/api/handoff/orm')
  end

  def emergency_email
    ENV.fetch('OS_EMERGENCY_EMAIL', '').strip.downcase.presence
  end

  def emergency_password_hash
    ENV.fetch('OS_EMERGENCY_PASSWORD_HASH', '').strip.presence
  end

  def emergency_until_raw
    ENV.fetch('OS_EMERGENCY_LOGIN_UNTIL', '').strip.presence
  end

  def emergency_until
    raw = emergency_until_raw
    return if raw.blank?

    Time.iso8601(raw)
  rescue ArgumentError
    nil
  end

  # On only while OS_EMERGENCY_LOGIN_UNTIL is in the future and at most 24 hours
  # ahead, and the email and a bcrypt hash of cost >= 12 are configured (3.10).
  def emergency_login_on?(now = Time.current)
    deadline = emergency_until
    return false if deadline.nil? || emergency_email.nil? || !emergency_hash_usable?

    deadline > now && deadline <= now + EMERGENCY_MAX_AHEAD
  end

  def emergency_hash_usable?
    hash = emergency_password_hash
    return false if hash.nil?

    BCrypt::Password.new(hash).cost >= MIN_BCRYPT_COST
  rescue BCrypt::Errors::InvalidHash
    false
  end

  def report_url
    ENV.fetch('OS_REPORT_URL', '').strip.presence
  end

  def report_key_id
    ENV.fetch('OS_REPORT_KEY_ID', '').strip.presence
  end

  # The HMAC key is the 32 decoded bytes, not the text (3.6).
  def report_key
    raw = ENV.fetch('OS_REPORT_KEY', '').strip
    return if raw.empty?

    padded = raw + ('=' * ((4 - (raw.length % 4)) % 4))
    key = Base64.urlsafe_decode64(padded)
    raise InvalidReportKey, 'OS_REPORT_KEY must decode to exactly 32 bytes' unless key.bytesize == 32

    key
  rescue ArgumentError
    raise InvalidReportKey, 'OS_REPORT_KEY is not base64url'
  end

  def reporter_enabled?
    report_url.present? && report_key_id.present? && report_key.present?
  end

  # Called at boot: an app refuses to start with a report key that is not 32 bytes (3.6).
  def validate!
    report_key
    true
  end

  def switch_on?(name)
    ON_VALUES.include?(ENV.fetch(name, '').strip.downcase)
  end
end
