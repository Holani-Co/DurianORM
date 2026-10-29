# Durian OS keys on users.custom_attributes (ORM appendix 10.8): os_person_id,
# os_kind (service | emergency; absent for a person) and os_offboarded_at.
# Only the Platform update writes them.
module Custom::Concerns::User
  extend ActiveSupport::Concern

  PROTECTED_KINDS = %w[service emergency].freeze

  def os_person_id
    (custom_attributes || {})['os_person_id'].presence
  end

  def durian_os_kind
    (custom_attributes || {})['os_kind'].presence || 'person'
  end

  def durian_os_emergency?
    durian_os_kind == 'emergency'
  end

  def durian_os_protected?
    PROTECTED_KINDS.include?(durian_os_kind)
  end

  # Checks and deletes the one-time login token with a single DEL, so two posts
  # of the same token can never both sign in (code change 2; F18).
  def consume_sso_auth_token(token)
    return false if token.blank?

    ::Redis::Alfred.delete(sso_token_key(token)).to_i == 1
  end

  # The emergency account's sessions end as soon as the emergency login goes off (3.10).
  def valid_token?(token, client = 'default')
    return false if durian_os_emergency? && !DurianOs::Config.emergency_login_on?

    super
  end
end
