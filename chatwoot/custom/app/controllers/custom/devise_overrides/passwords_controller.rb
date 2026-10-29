# With OS_SIGNIN_ONLY on, password reset is off (ORM appendix code change 4; F5).
# It stays off while the emergency login is on too: the emergency password lives only
# in OS_EMERGENCY_PASSWORD_HASH (3.10), and update would sign any user in.
module Custom::DeviseOverrides::PasswordsController
  def create
    return head(:not_found) if DurianOs::Config.signin_only?

    super
  end

  def update
    return head(:not_found) if DurianOs::Config.signin_only?

    super
  end
end
