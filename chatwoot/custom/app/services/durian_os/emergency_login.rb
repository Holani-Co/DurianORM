# The one emergency admin login (contract 3.10, ORM appendix 10.8).
# The password is checked against OS_EMERGENCY_PASSWORD_HASH, never the database,
# under a lockout of 5 failures per 15 minutes per address and 20 in total.
class DurianOs::EmergencyLogin
  SESSION_LIFESPAN = 1.hour
  MAX_FAILURES_PER_IP = 5
  IP_WINDOW = 15.minutes
  MAX_FAILURES_TOTAL = 20
  KEY_PREFIX = 'DURIAN_OS_EMERGENCY_FAILURES'.freeze

  def self.emergency_email?(email)
    configured = DurianOs::Config.emergency_email
    configured.present? && email.to_s.strip.downcase == configured
  end

  def self.emergency_user
    email = DurianOs::Config.emergency_email
    return if email.blank?

    user = User.from_email(email)
    user if user&.durian_os_emergency?
  end

  # Writes the log line and the emergency_login.used report. The sessions path gets
  # its audit row from Enterprise's sign_in audit; other paths ask for one here.
  def self.record_use!(user, via:, ip:, audit: false)
    Rails.logger.warn("[DurianOS] emergency login used via=#{via} user_id=#{user.id} ip=#{ip}")
    create_audit(user, via) if audit
    DurianOs::Reporter.report_changes([{
                                        kind: 'emergency_login.used',
                                        localUserId: user.id.to_s,
                                        actor: { localUserId: user.id.to_s, email: nil, via: 'emergency_login' },
                                        at: Time.now.utc.iso8601
                                      }])
  end

  def self.create_audit(user, via)
    return unless user.respond_to?(:audits)

    # Pass the user itself, so Audited never asks the (super admin) controller for current_user.
    user.audits.create(action: "emergency_#{via}", user: user,
                       associated_id: DurianOs::Config.account_id, associated_type: 'Account')
  end

  def initialize(email:, password:, ip:)
    @email = email
    @password = password.to_s
    @ip = ip.to_s
  end

  # Returns the emergency user, or nil. A nil counts as a failure unless locked.
  def authenticate
    return unless DurianOs::Config.emergency_login_on?
    return if locked?

    user = self.class.emergency_user
    return user if self.class.emergency_email?(@email) && user && password_matches?

    record_failure
    nil
  end

  def locked?
    count(ip_key) >= MAX_FAILURES_PER_IP || count(total_key) >= MAX_FAILURES_TOTAL
  end

  private

  def password_matches?
    return false if @password.empty?

    BCrypt::Password.new(DurianOs::Config.emergency_password_hash).is_password?(@password)
  rescue BCrypt::Errors::InvalidHash
    false
  end

  def record_failure
    increment(ip_key, IP_WINDOW)
    # The total lock holds until OS_EMERGENCY_LOGIN_UNTIL changes; the window is at most 24 hours.
    increment(total_key, DurianOs::Config::EMERGENCY_MAX_AHEAD + 1.hour)
  end

  def increment(key, ttl)
    value = Redis::Alfred.incr(key)
    Redis::Alfred.expire(key, ttl.to_i) if value == 1
  end

  def count(key)
    Redis::Alfred.get(key).to_i
  end

  # The window is keyed by the setting's value, so changing it clears the lock.
  def window_id
    Digest::SHA256.hexdigest(DurianOs::Config.emergency_until_raw.to_s)[0, 16]
  end

  def ip_key
    "#{KEY_PREFIX}::#{window_id}::ip::#{@ip}"
  end

  def total_key
    "#{KEY_PREFIX}::#{window_id}::total"
  end
end
