# With OS_SIGNIN_ONLY on, the emergency user cannot mint a new API token or change
# itself (name, email, password) (ORM appendix code change 12; contract 3.10).
module Custom::Api::V1::ProfilesController
  def update
    return head(:forbidden) if durian_os_emergency_profile?

    super
  end

  def reset_access_token
    return head(:forbidden) if durian_os_emergency_profile?

    super
  end

  private

  def durian_os_emergency_profile?
    DurianOs::Config.signin_only? && @user.durian_os_emergency?
  end
end
