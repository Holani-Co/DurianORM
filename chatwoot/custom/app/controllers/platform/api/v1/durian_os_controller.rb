# What the Durian OS connector cannot read from Chatwoot's own APIs
# (ORM appendix code change 11; contract 4.12). Behind the Platform App token.
class Platform::Api::V1::DurianOsController < PlatformController
  before_action(only: [:end_sessions]) { set_resource }
  before_action(only: [:end_sessions]) { validate_platform_app_permissible }

  # GET /platform/api/v1/durian_os/status
  def status
    render json: { emergencyLogin: { enabled: DurianOs::Config.emergency_login_on? } }
  end

  # POST /platform/api/v1/durian_os/users/:id/end_sessions
  def end_sessions
    render json: DurianOs::Sessions.end_all!(@resource)
  end

  private

  def set_resource
    @resource = User.find(params[:id])
  end
end
