# A private note an AI connection adds is marked `via_ai: <client>` in its content attributes (docs/mcp/MCP.md,
# section 10). Prepended by Message.prepend_mod_with; only while an AI request is served (DurianOs::Current.ai).
module Custom::Message
  def self.prepended(base)
    base.before_create :durian_os_mark_ai_note
  end

  private

  def durian_os_mark_ai_note
    caller = DurianOs::Current.ai
    return unless caller && private? && sender.is_a?(User) && sender.id == caller.user_id

    self.content_attributes = (content_attributes || {}).merge('via_ai' => caller.client)
  end
end
