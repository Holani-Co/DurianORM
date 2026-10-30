# The View as of the request being served (shared View as spec 3): set from the session
# token by Custom::Concerns::ApplicationControllerConcern and the sessions controller, read
# by the audit hook. Rails resets it after every request and job.
class DurianOs::Current < ActiveSupport::CurrentAttributes
  attribute :view_as, :user
end
