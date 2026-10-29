# The super admin console (ORM appendix code change 7; F6).
# With OS_SIGNIN_ONLY on it answers 404 unless the emergency login is on, and then
# accepts only OS_EMERGENCY_EMAIL, checked against OS_EMERGENCY_PASSWORD_HASH under
# the 3.10 lockout. A SuperAdmin's own database password is never accepted.
# Without the switch, the emergency email is still checked only against the hash.
module Custom::SuperAdmin::Devise::SessionsController
  SESSION_STARTED_KEY = :durian_os_emergency_started_at

  def new
    return head(:not_found) if durian_os_console_closed?

    super
  end

  def create
    return head(:not_found) if durian_os_console_closed?

    super
    session[SESSION_STARTED_KEY] = Time.current.to_i if @durian_os_emergency
  end

  private

  def durian_os_console_closed?
    DurianOs::Config.signin_only? && !DurianOs::Config.emergency_login_on?
  end

  def valid_credentials?
    email = params.dig(:super_admin, :email)
    emergency = DurianOs::EmergencyLogin.emergency_email?(email)
    return super unless emergency || DurianOs::Config.signin_only?
    return durian_os_invalid_credentials unless emergency && DurianOs::Config.emergency_login_on?

    user = DurianOs::EmergencyLogin.new(email: email, password: params.dig(:super_admin, :password), ip: request.remote_ip).authenticate
    @super_admin = user && SuperAdmin.find_by(id: user.id)
    return durian_os_invalid_credentials if @super_admin.blank?

    @durian_os_emergency = true
    DurianOs::EmergencyLogin.record_use!(user, via: 'super_admin_sign_in', ip: request.remote_ip, audit: true)
    true
  end

  def durian_os_invalid_credentials
    @error_message = 'Invalid credentials. Please try again.'
    false
  end
end
