# Durian OS "View as" for ORM (shared View as spec, 30 Oct 2026), behind OS_VIEW_AS.
#
# ORM has no signed hand-off: Durian OS opens ORM with a one-time login link from the
# Platform API. For a View as it asks POST /platform/api/v1/durian_os/users/:id/view_as_login,
# which makes the link with impersonation=true and records the View as against the link's
# one-time token in Redis. When the link is used, the sessions controller takes that record
# and the session it makes:
# - expires at imp_exp (never later than the normal lifetime) and cannot be renewed past it;
# - carries the admin (by_email, by_name) and imp in its users.tokens entry ("os_view_as");
# - is refused for the actions in BLOCKED_ACTIONS;
# - marks the audit rows written while it is used (comment) and logs every request
#   (DurianOs::ViewAsLog).
module DurianOs::ViewAs
  BLOCKED_MESSAGE = 'Not available while viewing as someone.'.freeze
  TOKEN_KEY = 'os_view_as'.freeze
  RECORD_KEY = 'DURIAN_OS_VIEW_AS::%<user_id>d::%<digest>s'.freeze
  # Outlives the one-time token's 5 minutes, so a live token always finds its record.
  RECORD_TTL = 6.minutes
  # Durian OS ends a View as after 60 minutes at most; allow a little clock skew.
  MAX_AHEAD = 65.minutes
  MAX_TEXT = 255

  # Refused while the session is a View as (controller_path => actions): password change,
  # set and reset; API token reset; MFA; availability and profile/settings writes; push
  # subscriptions (they would keep the person's notifications coming after the View as).
  BLOCKED_ACTIONS = {
    'api/v1/profiles' => %w[update avatar availability auto_offline reset_access_token],
    'api/v1/profile/mfa' => %w[create destroy verify backup_codes],
    'api/v1/notification_subscriptions' => %w[create destroy],
    'devise_token_auth/registrations' => %w[create update destroy],
    'devise_overrides/passwords' => %w[create update]
  }.freeze

  class Refused < StandardError
    attr_reader :code

    def initialize(code)
      @code = code
      super(code)
    end
  end

  Session = Struct.new(:by_email, :by_name, :imp, :imp_exp, keyword_init: true) do
    def self.from_h(data)
      return unless data.is_a?(Hash)

      data = data.stringify_keys
      session = new(by_email: data['by_email'].presence, by_name: data['by_name'].presence,
                    imp: data['imp'].presence, imp_exp: Integer(data['imp_exp'].to_s, exception: false))
      session if session.by_email && session.imp && session.imp_exp
    end

    def ends_at
      Time.zone.at(imp_exp)
    end

    def ended?(now = Time.current)
      imp_exp <= now.to_i
    end

    def admin_label
      by_name.present? ? "#{by_name} <#{by_email}>" : by_email
    end

    def impersonated_by
      { email: by_email, name: by_name }
    end

    def to_record
      { 'by_email' => by_email, 'by_name' => by_name, 'imp' => imp, 'imp_exp' => imp_exp }
    end
  end

  module_function

  # Checks the Platform request body. Raises Refused with an error code.
  def build(params, now: Time.current)
    session = Session.new(by_email: text(params[:by_email]).downcase.presence, by_name: text(params[:by_name]).presence,
                          imp: text(params[:imp]).presence, imp_exp: parse_time(params[:imp_exp]))
    raise Refused, 'invalid_view_as' unless session.by_email && session.imp && session.imp_exp
    raise Refused, 'view_as_ended' if session.ended?(now)
    raise Refused, 'view_as_too_long' if session.imp_exp > (now + MAX_AHEAD).to_i

    session
  end

  def text(value)
    value.to_s.strip.first(MAX_TEXT)
  end

  def parse_time(value)
    Float(value.to_s, exception: false)&.then { |number| number.to_i if number.finite? }
  end

  # The one-time login link with impersonation=true, with the View as recorded against its token.
  def create_link!(user, session)
    url = user.generate_sso_link_with_impersonation
    token = Rack::Utils.parse_query(URI.parse(url).query)['sso_auth_token']
    begin
      Redis::Alfred.setex(record_key(user, token), session.to_record.to_json, RECORD_TTL)
    rescue StandardError
      user.invalidate_sso_auth_token(token)
      raise
    end
    DurianOs::ViewAsLog.link(user, session)
    url
  end

  # Called once the one-time token has been consumed. Returns nil for a normal link,
  # the Session for a View as that may sign in, or :refused for a View as that must not
  # (OS_VIEW_AS off, or the View as has ended). A View as never signs in unmarked.
  def take(user, token, now: Time.current)
    key = record_key(user, token)
    raw = Redis::Alfred.get(key)
    return if raw.nil?

    Redis::Alfred.delete(key)
    session = Session.from_h(JSON.parse(raw))
    return :refused if session.nil? || !DurianOs::Config.view_as? || session.ended?(now)

    session
  rescue JSON::ParserError
    :refused
  end

  # Seconds the session token may live: until imp_exp, never longer than asked or than normal.
  def lifespan(session, requested = nil, now: Time.current)
    limit = (requested || DeviseTokenAuth.token_lifespan).to_i
    [session.imp_exp - now.to_i, limit].min.seconds
  end

  def token_data(entry)
    return unless entry.is_a?(Hash)

    entry[TOKEN_KEY] || entry[TOKEN_KEY.to_sym]
  end

  # The View as of a session (users.tokens entry for the client), or nil.
  def session_for(user, client)
    return unless user.is_a?(User) && client.present? && user.tokens.is_a?(Hash)

    Session.from_h(token_data(user.tokens[client]))
  end

  # A View as session token works only while OS_VIEW_AS is on and before imp_exp.
  def token_usable?(entry, now: Time.current)
    data = token_data(entry)
    return true if data.nil?

    session = Session.from_h(data)
    session.present? && DurianOs::Config.view_as? && !session.ended?(now)
  end

  def blocked_action?(controller_path, action_name)
    BLOCKED_ACTIONS.fetch(controller_path.to_s, []).include?(action_name.to_s)
  end

  def record_key(user, token)
    format(RECORD_KEY, user_id: user.id, digest: Digest::SHA256.hexdigest(token.to_s))
  end
end
