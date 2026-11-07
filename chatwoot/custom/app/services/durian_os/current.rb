# The View as of the request being served (shared View as spec 3): set from the session
# token by Custom::Concerns::ApplicationControllerConcern and the sessions controller, read
# by the audit hook. Rails resets it after every request and job.
# `ai` is the caller of an AI request (DurianOs::Ai::Caller), set by DurianOs::Ai::Door around
# the request and read by Custom::AccountUser for the ceiling (docs/mcp/MCP.md, section 4).
class DurianOs::Current < ActiveSupport::CurrentAttributes
  attribute :view_as, :user, :ai
end
