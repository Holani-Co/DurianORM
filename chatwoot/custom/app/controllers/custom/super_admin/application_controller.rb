# With OS_SIGNIN_ONLY on, a console session lasts at most one hour, must have been
# started by the emergency login, and ends as soon as that login goes off (3.10).
module Custom::SuperAdmin::ApplicationController
  def self.prepended(base)
    base.prepend_before_action :durian_os_end_stale_console_session
  end

  private

  def durian_os_end_stale_console_session
    return unless DurianOs::Config.signin_only? && super_admin_signed_in?

    started = session[Custom::SuperAdmin::Devise::SessionsController::SESSION_STARTED_KEY].to_i
    fresh = started.positive? && Time.current.to_i - started < DurianOs::EmergencyLogin::SESSION_LIFESPAN.to_i
    return if fresh && DurianOs::Config.emergency_login_on?

    sign_out(:super_admin)
    head :not_found
  end
end
