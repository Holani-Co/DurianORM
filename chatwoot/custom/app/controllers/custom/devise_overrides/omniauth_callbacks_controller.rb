# With OS_SIGNIN_ONLY on, Google and SAML sign-in and sign-up send the person to
# Durian OS instead (ORM appendix code change 3). Gmail inboxes use a different
# controller and keep working.
module Custom::DeviseOverrides::OmniauthCallbacksController
  private

  def sign_in_user
    return durian_os_redirect if DurianOs::Config.signin_only?

    super
  end

  def sign_in_user_on_mobile
    return durian_os_redirect if DurianOs::Config.signin_only?

    super
  end

  def sign_up_user
    return durian_os_redirect if DurianOs::Config.signin_only?

    super
  end

  def durian_os_redirect
    redirect_to DurianOs::Config.handoff_url, allow_other_host: true
  end
end
