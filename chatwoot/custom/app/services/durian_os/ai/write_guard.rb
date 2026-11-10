# Extra checks on an offered write, before its route runs (docs/mcp/MCP.md, section 6). The registry names one in
# `guard`; the gate calls it with the person's own Pundit context, so ORM's own policies decide who sees what:
# - conversation_attributes / contact_attributes: only keys of the account's defined custom attributes, so an AI never
#   overwrites what the Zoho bridge stores in the same column (zoho_tickets, email_category_v2, crm_contact_id, ...);
# - labels_known: a label not already on the record must exist in the account's labels (no stray tags);
# - macro_safe: the macro is the person's to run, every action in it stays inside ORM (no message, email, file or
#   webhook), and it runs only on up to 10 conversations the person can open (the job itself checks none of that).
# Returns nil, or [status, code, words] for the refusal.
module DurianOs::Ai::WriteGuard
  Context = Struct.new(:entry, :account, :user, :account_user, :params, :body, keyword_init: true) do
    def policy_context
      { user: user, account: account, account_user: account_user }
    end
  end

  SAFE_MACRO_ACTIONS = %w[add_label remove_label assign_team assign_agent mute_conversation change_status remove_assigned_agent
                          remove_assigned_team resolve_conversation snooze_conversation change_priority add_private_note].freeze
  MAX_MACRO_CONVERSATIONS = 10
  MACRO_SENDS = 'This macro sends something outside ORM (a message, email, file or webhook), so it can only be run in ORM itself.'.freeze
  MACRO_TOO_MANY = "A macro can be run on 1 to #{MAX_MACRO_CONVERSATIONS} conversations at a time.".freeze
  UNDEFINED_ATTRIBUTE = 'Only the account\'s defined custom attributes can be set: %<keys>s is not one. See list_custom_attribute_definitions.'.freeze
  UNKNOWN_LABEL = 'These labels do not exist in ORM: %<labels>s. Create them first, or use labels from list_labels.'.freeze

  module_function

  def check(context)
    case context.entry.guard
    when 'conversation_attributes' then attributes(context, 'conversation_attribute')
    when 'contact_attributes' then attributes(context, 'contact_attribute')
    when 'labels_known' then labels_known(context)
    when 'macro_safe' then macro_safe(context)
    end
  end

  def attributes(context, model)
    value = context.body['custom_attributes']
    return if value.nil?

    keys = value.is_a?(Hash) ? value.keys : Array(value)
    defined = context.account.custom_attribute_definitions.where(attribute_model: model).pluck(:attribute_key)
    stray = keys.map(&:to_s) - defined
    return if stray.empty?

    [422, 'bad_input', format(DurianOs::Ai::WriteGuard::UNDEFINED_ATTRIBUTE, keys: stray.first(5).join(', '))]
  end

  def labels_known(context)
    wanted = Array(context.body['labels']).map(&:to_s)
    record = labelled(context)
    return [404, 'not_found', DurianOs::Ai::NOT_FOUND] if record.nil?

    new_labels = wanted - record.label_list
    missing = new_labels - context.account.labels.where(title: new_labels).pluck(:title)
    return if missing.empty?

    [422, 'bad_input', format(UNKNOWN_LABEL, labels: missing.first(5).join(', '))]
  end

  def labelled(context)
    if context.params[:conversation_id]
      context.account.conversations.find_by(display_id: context.params[:conversation_id])
    else
      context.account.contacts.find_by(id: context.params[:contact_id])
    end
  end

  def macro_safe(context)
    macro = context.account.macros.find_by(id: context.params[:id])
    return [404, 'not_found', DurianOs::Ai::NOT_FOUND] if macro.nil?
    return [403, 'role', DurianOs::Ai::ROLE_REFUSED] unless MacroPolicy.new(context.policy_context, macro).execute?

    names = Array(macro.actions).map { |action| action.to_h.with_indifferent_access[:action_name].to_s }
    return [403, 'write_refused', MACRO_SENDS] unless names.all? { |name| SAFE_MACRO_ACTIONS.include?(name) }

    macro_conversations(context)
  end

  def macro_conversations(context)
    ids = Array(context.body['conversation_ids']).uniq
    return [422, 'bad_input', MACRO_TOO_MANY] unless ids.size.between?(1, MAX_MACRO_CONVERSATIONS)

    found = context.account.conversations.where(display_id: ids).to_a
    visible = found.select { |conversation| ConversationPolicy.new(context.policy_context, conversation).show? }
    [404, 'not_found', DurianOs::Ai::NOT_FOUND] unless visible.size == ids.size
  end
end
