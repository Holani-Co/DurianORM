# What an AI connection changes shows in the conversation's activity as "<person> via <client>" (docs/mcp/MCP.md,
# section 10), wherever Chatwoot names the person who acted: status, priority, labels, SLA, assignee, team, mute.
# Prepended by ActivityMessageHandler.prepend_mod_with. Nothing else about activity messages changes.
module Custom::ActivityMessageHandler
  private

  def determine_user_name
    durian_os_via(super)
  end

  def activity_message_owner(user_name)
    durian_os_via(super)
  end

  def create_mute_change_activity(change_type)
    return super unless DurianOs::Current.ai && Current.user

    content = I18n.t("conversations.activity.#{change_type}", user_name: durian_os_via(Current.user.name))
    ::Conversations::ActivityMessageJob.perform_later(self, activity_message_params(content))
  end

  def durian_os_via(name)
    caller = DurianOs::Current.ai
    return name if name.blank? || caller.nil? || name.to_s.end_with?(" via #{caller.client}")

    "#{name} via #{caller.client}"
  end
end
