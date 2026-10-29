# With OS_SIGNIN_ONLY on, the emergency user's API token is refused (ORM appendix code change 12; F7).
module Custom::AccessTokenAuthHelper
  def ensure_access_token
    super
    owner = @access_token&.owner
    @access_token = nil if DurianOs::Config.signin_only? && owner.is_a?(User) && owner.durian_os_emergency?
  end
end
