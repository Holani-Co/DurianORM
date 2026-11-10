# Extra checks on an offered write, before its route runs (docs/mcp/MCP.md, section 6). The registry names one in
# `guard`; the gate calls it with the person's own Pundit context, so ORM's own policies decide who sees what:
# - conversation_attributes / contact_attributes: only keys of the account's defined custom attributes, so an AI never
#   overwrites what the Zoho bridge stores in the same column (zoho_tickets, email_category_v2, crm_contact_id, ...);
# - labels_known: a label not already on the record must exist in the account's labels (no stray tags);
# - assignment: an agent of the account (never the DurianAI bot login, whose assignment makes the Zoho bridge's social
#   agent answer the customer) or a team of the account; never a null, which would take the assignment away.
# Returns nil, or [status, code, words] for the refusal.
module DurianOs::Ai::WriteGuard
  Context = Struct.new(:entry, :account, :user, :account_user, :params, :body, keyword_init: true) do
    def policy_context
      { user: user, account: account, account_user: account_user }
    end
  end

  UNDEFINED_ATTRIBUTE = 'Only the account\'s defined custom attributes can be set: %<keys>s is not one. See list_custom_attribute_definitions.'.freeze
  BOT_ASSIGNMENT = 'Assigning the DurianAI bot makes it answer the customer, so that stays in ORM.'.freeze
  ASSIGN_WHO = 'Give an assignee_id (an agent of the account) or a team_id (a team of the account), as a number.'.freeze
  UNKNOWN_LABEL = 'These labels do not exist in ORM: %<labels>s. Create them first, or use labels from list_labels.'.freeze

  module_function

  def check(context)
    case context.entry.guard
    when 'conversation_attributes' then attributes(context, 'conversation_attribute')
    when 'contact_attributes' then attributes(context, 'contact_attribute')
    when 'labels_known' then labels_known(context)
    when 'assignment' then assignment(context)
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

  def assignment(context)
    body = context.body
    if body.key?('assignee_id')
      agent = context.account.users.find_by(id: body['assignee_id']) if positive_id(body['assignee_id'])
      return [422, 'bad_input', ASSIGN_WHO] if agent.nil?

      [403, 'write_refused', BOT_ASSIGNMENT] if bot?(agent)
    elsif body.key?('team_id')
      known = positive_id(body['team_id']) && context.account.teams.exists?(id: body['team_id'])
      [422, 'bad_input', ASSIGN_WHO] unless known
    else
      [422, 'bad_input', ASSIGN_WHO]
    end
  end

  def positive_id(value)
    value.is_a?(Integer) && value.positive?
  end

  # The bridge's own login (zoho-bridge SOCIAL_AGENT_BOT_AGENT_NAMES, default DurianAI), compared the way the bridge does.
  def bot?(user)
    names = ENV.fetch('SOCIAL_AGENT_BOT_AGENT_NAMES', 'DurianAI').split(',').filter_map { |name| normal(name).presence }
    [user.name, user.available_name].any? { |name| names.include?(normal(name)) }
  end

  def normal(name)
    name.to_s.downcase.gsub(/[^a-z0-9]/, '')
  end
end
