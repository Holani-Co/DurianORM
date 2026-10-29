# /app/login (ORM appendix code change 5).
# - With OS_SIGNIN_ONLY on, the login page without a one-time token redirects to
#   Durian OS, except /app/login?emergency=1 while the emergency login is on.
#   A one-minute cookie stops redirect loops.
# - A page carrying the one-time token is never cached and sends no referrer (3.8).
module Custom::DashboardController
  LOGIN_PATH = '/app/login'.freeze
  LOOP_COOKIE = :durian_os_login_redirect

  def self.prepended(base)
    base.before_action :durian_os_login_gate, only: [:index]
  end

  private

  def durian_os_login_gate
    return unless request.path.chomp('/') == LOGIN_PATH

    if params[:sso_auth_token].present?
      response.headers['Cache-Control'] = 'no-store'
      response.headers['Referrer-Policy'] = 'no-referrer'
      return
    end
    return unless DurianOs::Config.signin_only?
    return if params[:emergency] == '1' && DurianOs::Config.emergency_login_on?
    return if cookies[LOOP_COOKIE].present?

    cookies[LOOP_COOKIE] = { value: '1', expires: 1.minute, httponly: true, same_site: :lax }
    redirect_to DurianOs::Config.handoff_url, allow_other_host: true
  end
end
