# With OS_SIGNIN_ONLY on, the Platform API makes no one-time login link and hands out no
# API token for the emergency user (ORM appendix code change 12). It signs in only with
# the emergency login.
module Custom::Platform::Api::V1::UsersController
  def login
    return durian_os_refuse_emergency if durian_os_emergency_resource?

    super
  end

  def token
    return durian_os_refuse_emergency if durian_os_emergency_resource?

    super
  end

  private

  def durian_os_emergency_resource?
    DurianOs::Config.signin_only? && @resource.durian_os_emergency?
  end

  def durian_os_refuse_emergency
    render json: { error: 'not_linkable' }, status: :forbidden
  end
end
