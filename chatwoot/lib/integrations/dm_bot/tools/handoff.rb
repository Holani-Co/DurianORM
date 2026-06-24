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

  description 'Hand the conversation off to a human agent. ' \
              'Call IMMEDIATELY for fraud/scam accusations, legal threats, abuse, or anything ' \
              'outside what you can help with. ' \
              'For an ORDER ISSUE (complaint, defect, damage, delay, delivery, or existing-order ' \
              'status): DO NOT call this on the first message — first ask the customer for their ' \
              'order ID / order number and registered mobile number. Call this ONLY after those ' \
              'details appear in the conversation (or the customer has explicitly refused to share them).'

  param :rule, desc: 'Which rule triggered the handoff — one of: ' \
                     'order_details_collected, order_details_refused, refund_or_payment, ' \
                     'legal_threat, abuse_or_spam, out_of_catalog, other', required: true
  param :reason, desc: 'One short sentence explaining, in context, why this specific message needs a human', required: true

  # rule/reason are captured from the tool call for the trace; the processor
  # performs the actual bot_handoff!. This only acknowledges the decision.
  def execute(**)
    'Handoff registered — a human agent will take over.'
  end
end
