# A person's access in the managed account, as the Durian OS connector reads it
# (ORM appendix 10.8, "Where access lives" and "Access object").
#
# version = first 16 base64url characters of
#   SHA-256("<state>|<role>|<custom_role_id or empty>|<sorted inbox ids, comma-joined>|<sorted team ids, comma-joined>")
# The connector computes the same string from the Account API, so a report of a
# change Durian OS made itself carries the version it already holds (6.2).
class DurianOs::AccessSnapshot
  attr_reader :user_id, :account_id

  def self.version_for(state:, role:, custom_role_id:, inbox_ids:, team_ids:)
    source = [state, role, custom_role_id.to_s, inbox_ids.map(&:to_i).sort.join(','), team_ids.map(&:to_i).sort.join(',')].join('|')
    Base64.urlsafe_encode64(Digest::SHA256.digest(source), padding: false)[0, 16]
  end

  def initialize(user_id, account_id = DurianOs::Config.account_id)
    @user_id = user_id
    @account_id = account_id
  end

  def account_user
    @account_user ||= AccountUser.find_by(account_id: account_id, user_id: user_id)
  end

  def user
    @user ||= User.find_by(id: user_id)
  end

  def state
    return 'active' if account_user
    return 'offboarded' if user&.custom_attributes&.dig('os_offboarded_at').present?

    'inactive'
  end

  # Non-members read as role agent (F13).
  def role
    account_user&.role || 'agent'
  end

  def custom_role_id
    account_user.try(:custom_role_id)
  end

  def inbox_ids
    InboxMember.joins(:inbox).where(user_id: user_id, inboxes: { account_id: account_id }).pluck(:inbox_id)
  end

  def team_ids
    TeamMember.joins(:team).where(user_id: user_id, teams: { account_id: account_id }).pluck(:team_id)
  end

  def version
    self.class.version_for(state: state, role: role, custom_role_id: custom_role_id, inbox_ids: inbox_ids, team_ids: team_ids)
  end
end
