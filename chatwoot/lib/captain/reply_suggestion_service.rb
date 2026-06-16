class Captain::ReplySuggestionService < Captain::BaseTaskService
  pattr_initialize [:account!, :conversation_display_id!, :user!]

  def perform
    make_api_call(
      model: GPT_MODEL,
      messages: [
        { role: 'system', content: system_prompt },
        { role: 'user', content: formatted_conversation }
      ]
    )
  end

  private

  def system_prompt
    template = prompt_from_file('reply')
    render_liquid_template(template, prompt_variables)
  end

  def prompt_variables
    # customer_name lets the prompt template address the customer by their
    # real name instead of falling back to an "[Customer's Name]" placeholder
    # the LLM has been observed to insert when no name is available. Falls
    # back to '' when the contact has no name on record — the template then
    # instructs the model to omit the salutation rather than invent one.
    {
      'channel_type' => conversation.inbox.channel_type,
      'agent_name' => user.name,
      'agent_signature' => user.message_signature.presence,
      'customer_name' => conversation.contact&.name.presence || ''
    }
  end

  def render_liquid_template(template_content, variables = {})
    Liquid::Template.parse(template_content).render(variables)
  end

  def formatted_conversation
    LlmFormatter::ConversationLlmFormatter.new(conversation).format(token_limit: TOKEN_LIMIT)
  end

  def event_name
    'reply_suggestion'
  end
end

Captain::ReplySuggestionService.prepend_mod_with('Captain::ReplySuggestionService')
