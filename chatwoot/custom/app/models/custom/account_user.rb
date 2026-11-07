# The ceiling of a Durian OS AI connection (docs/mcp/MCP.md, section 4), prepended by
# AccountUser.prepend_mod_with('AccountUser').
#
# While an AI request is served (DurianOs::Current.ai is set) with a pass below `administer`, an administrator acts as
# a plain agent with no custom role: only on the caller's own rows, and only when the stored role is administrator.
# Agents, with a custom role or not, are unchanged. Every role check that decides what a person sees reads an
# AccountUser object (Pundit's account_user, Api::BaseController#check_admin_authorization?, User#administrator?,
# #assigned_inboxes, ConversationFinder, Conversations::PermissionFilterService and its custom-role filter), so they
# all see the lowered role. Other people's rows, and SQL on account_users.role, are never changed.
module Custom::AccountUser
  def role
    durian_os_ai_lowered? ? 'agent' : super
  end

  def administrator?
    durian_os_ai_lowered? ? false : super
  end

  def agent?
    durian_os_ai_lowered? || super
  end

  def custom_role_id
    durian_os_ai_lowered? ? nil : super
  end

  def custom_role
    durian_os_ai_lowered? ? nil : super
  end

  def permissions
    durian_os_ai_lowered? ? ['agent'] : super
  end

  private

  def durian_os_ai_lowered?
    caller = DurianOs::Current.ai
    return false if caller.nil? || caller.full_access?
    return false unless has_attribute?(:user_id) && has_attribute?(:role)

    self[:user_id] == caller.user_id && self[:role] == 'administrator'
  end
end
