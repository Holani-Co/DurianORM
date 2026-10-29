# /app/login (ORM appendix code change 5).
# - With OS_SIGNIN_ONLY on, the login page without a one-time token is the shared Durian
#   login (email + password checked by Durian OS, or Sign in with Durian OS), except
#   /app/login?emergency=1 while the emergency login is on.
# - A page carrying the one-time token is never cached and sends no referrer (3.8).
module Custom::DashboardController
  LOGIN_PATH = '/app/login'.freeze

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

    durian_os_login_page
  end

  # The same login page as every Durian app (29 Oct 2026): email + password checked by Durian OS
  # (one password everywhere), or Sign in with Durian OS. No sign-up, no Google.
  def durian_os_login_page
    os = 'https://durianos.in'
    back = "#{request.base_url}#{LOGIN_PATH}"
    error = params[:os] == 'bad_password' ? '<p class="err">Wrong email or password.</p>' : ''
    response.headers['Cache-Control'] = 'no-store'
    render html: <<~HTML.html_safe, layout: false
      <!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>Durian · ORM</title><style>
      *{box-sizing:border-box}body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:linear-gradient(135deg,#1a1a2e 0%,#0f3460 100%)}
      .card{background:#fff;border-radius:16px;padding:40px 36px;width:100%;max-width:400px;box-shadow:0 20px 60px rgba(0,0,0,.3)}
      h1{margin:0 0 24px;text-align:center;font-size:22px;color:#1a1a2e}label{display:block;font-size:12px;font-weight:700;letter-spacing:.5px;text-transform:uppercase;color:#444;margin:14px 0 6px}
      input{width:100%;padding:12px 14px;border:2px solid #e0e0e0;border-radius:10px;font-size:14px}input:focus{outline:none;border-color:#0f3460}
      button,.os{display:block;width:100%;margin-top:20px;padding:13px;border-radius:10px;font-size:14px;font-weight:700;text-align:center;text-decoration:none;cursor:pointer}
      button{border:0;color:#fff;background:linear-gradient(135deg,#1a1a2e 0%,#0f3460 100%)}.os{margin-top:0;border:2px solid #0f3460;color:#0f3460;background:#fff}
      .or{display:flex;align-items:center;gap:12px;margin:20px 0;color:#999;font-size:11px;text-transform:uppercase;letter-spacing:1px}.or:before,.or:after{content:"";flex:1;height:1px;background:#e0e0e0}
      .foot{text-align:center;margin-top:20px;font-size:12px;color:#888}.err{background:#fee2e2;color:#991b1b;border-radius:8px;padding:10px 12px;font-size:13px;margin:0 0 8px}
      </style></head><body><div class="card"><h1>ORM</h1>#{error}
      <form method="POST" action="#{os}/api/auth/app-signin"><input type="hidden" name="app" value="orm"><input type="hidden" name="back" value="#{ERB::Util.html_escape(back)}">
      <label for="email">Email</label><input id="email" name="email" type="email" autocomplete="username" required>
      <label for="password">Password</label><input id="password" name="password" type="password" autocomplete="current-password" required>
      <button type="submit">Sign in</button></form>
      <div class="or">or</div><a class="os" href="#{ERB::Util.html_escape(DurianOs::Config.handoff_url)}">Sign in with Durian OS</a>
      <p class="foot">Contact your admin for access.</p></div></body></html>
    HTML
  end
end
