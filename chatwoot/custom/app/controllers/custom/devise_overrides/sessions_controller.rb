# Sign-in for ORM under Durian OS (ORM appendix code change 2).
# - The one-time login link from Durian OS (Platform login, SSO branch) always works,
#   and its token is consumed with one DEL.
# - The emergency email signs in only against OS_EMERGENCY_PASSWORD_HASH while
#   OS_EMERGENCY_LOGIN_UNTIL is on, for one hour; otherwise it gets 404.
# - With OS_SIGNIN_ONLY on, every other password or MFA post gets 404.
module Custom::DeviseOverrides::SessionsController
  def create
    return durian_os_emergency_sign_in if durian_os_emergency_request?
    return super unless DurianOs::Config.signin_only?
    return head(:not_found) if params[:sso_auth_token].blank? || params[:mfa_token].present?
    return durian_os_render_sso_failure if @resource.blank?

    super
  end

  private

  def handle_sso_authentication
    return durian_os_render_sso_failure if @resource.durian_os_emergency?
    return durian_os_render_sso_failure unless @resource.consume_sso_auth_token(params[:sso_auth_token])

    super
  end

  def durian_os_emergency_request?
    params[:sso_auth_token].blank? && params[:mfa_token].blank? && DurianOs::EmergencyLogin.emergency_email?(params[:email])
  end

  def durian_os_emergency_sign_in
    return head(:not_found) unless DurianOs::Config.emergency_login_on?

    @resource = DurianOs::EmergencyLogin.new(email: params[:email], password: params[:password], ip: request.remote_ip).authenticate
    return render_create_error_bad_credentials if @resource.blank?

    @token = @resource.create_token(lifespan: DurianOs::EmergencyLogin::SESSION_LIFESPAN)
    @resource.save!
    sign_in(:user, @resource, store: false, bypass: false)
    DurianOs::EmergencyLogin.record_use!(@resource, via: 'sign_in', ip: request.remote_ip, audit: !ChatwootApp.enterprise?)
    render_create_success
  end

  def durian_os_render_sso_failure
    render_error(:unauthorized, I18n.t('devise_token_auth.sessions.bad_credentials'))
  end
end
