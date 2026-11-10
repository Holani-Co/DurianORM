# GET /api/v1/ai/me (docs/mcp/MCP.md, section 9), the read tool get_my_orm_access: who this connection acts as in
# ORM's managed account (OS_ACCOUNT_ID), their own role and custom role, the role after the connection's ceiling
# (acts_as, permissions, inboxes, teams) and the read tools they may call: those whose `who` they pass after the
# ceiling and whose feature, if any, is on for the account. The MCP server's tools/list makes exactly one call here
# and lists exactly `tools`. A browser session gets the same shape (auth_kind session, no client or ceiling).
class Api::V1::AiController < Api::BaseController
  NOT_MEMBER = 'You have no ORM account in the Durian account.'.freeze

  def me
    user = Current.user
    return render(json: { error: NOT_MEMBER }, status: :forbidden) unless user.is_a?(User)

    account = Account.find(DurianOs::Config.account_id)
    account_user = account.account_users.find_by(user_id: user.id)
    return render(json: { error: NOT_MEMBER }, status: :forbidden) if account_user.nil?

    Current.account = account
    Current.account_user = account_user
    render json: access(user, account, account_user)
  end

  private

  def access(user, account, account_user)
    caller = request.env[DurianOs::Ai::ENV_KEY]
    {
      user: user.email, name: user.name, user_id: user.id, os_person_id: user.os_person_id,
      auth_kind: auth_kind(caller), client: caller&.client, account: { id: account.id, name: account.name },
      ceiling: caller&.ceiling, role: account_user[:role], custom_role: own_custom_role(account_user),
      acts_as: account_user.role, permissions: account_user.permissions,
      inboxes: user.assigned_inboxes.order(:id).map { |inbox| { id: inbox.id, name: inbox.name, channel_type: inbox.channel_type } },
      teams: user.teams.where(account_id: account.id).order(:id).map { |team| { id: team.id, name: team.name } },
      read_only: caller.present? && caller.read_only?,
      tools: DurianOs::Ai::Registry.tools_for(user, account, account_user, writes: caller.present? && caller.full_access?)
    }
  end

  # The person's own custom role, before the ceiling.
  def own_custom_role(account_user)
    role_id = account_user[:custom_role_id]
    custom_role = role_id && CustomRole.find_by(id: role_id, account_id: account_user.account_id)
    custom_role && { id: custom_role.id, name: custom_role.name }
  end

  def auth_kind(caller)
    return 'pass' if caller
    return 'token' if @access_token

    'session'
  end
end
