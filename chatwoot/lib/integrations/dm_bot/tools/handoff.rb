# frozen_string_literal: true

# Decision tool: hand off to a human. The `rule` param is what makes the
# handoff auditable in the trace — it ties the action back to a named rule from
# the prompt ("it was in the prompt"), instead of an opaque escalation. The
# processor detects this tool by name from the on_tool_call callback and runs
# the actual bot_handoff!; this tool only registers the decision + its reason.
class Integrations::DmBot::Tools::Handoff < Integrations::DmBot::Tools::Base
  def self.name
    'handoff'
  end

  description 'Hand the conversation off to a human agent. Use ONLY when you genuinely cannot help: ' \
              'requests outside the catalog, refunds/payment problems, or serious complaints, ' \
              'accusations, abuse, or legal threats.'

  param :rule, desc: 'Which rule triggered the handoff — one of: ' \
                     'out_of_catalog, refund_or_payment, legal_threat, abuse_or_spam, other', required: true
  param :reason, desc: 'One short sentence explaining, in context, why this specific message needs a human', required: true

  # rule/reason are captured from the tool call for the trace; the processor
  # performs the actual bot_handoff!. This only acknowledges the decision.
  def execute(**)
    'Handoff registered — a human agent will take over.'
  end
end
