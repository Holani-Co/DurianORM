# What the Durian OS connector cannot read from Chatwoot's own APIs
# (ORM appendix code change 11; contract 4.12). Behind the Platform App token.
class Platform::Api::V1::DurianOsController < PlatformController
  before_action(only: [:view_as_login]) { head(:not_found) unless DurianOs::Config.view_as? }
  before_action(only: [:end_sessions, :view_as_login]) { set_resource }
  before_action(only: [:end_sessions, :view_as_login]) { validate_platform_app_permissible }

  # GET /platform/api/v1/durian_os/status
  def status
    render json: { emergencyLogin: { enabled: DurianOs::Config.emergency_login_on? } }
  end

  # POST /platform/api/v1/durian_os/users/:id/end_sessions
  def end_sessions
    render json: DurianOs::Sessions.end_all!(@resource)
  end

  # POST /platform/api/v1/durian_os/users/:id/view_as_login (shared View as spec; OS_VIEW_AS)
  # Body: { by_email, by_name, imp, imp_exp (unix seconds) }. Answers { url }: a one-time
  # login link with impersonation=true whose session is marked with the admin and ends at imp_exp.
  def view_as_login
    refusal = view_as_refusal
    return render(json: { error: refusal }, status: :forbidden) if refusal

    session = DurianOs::ViewAs.build(params)
    render json: { url: DurianOs::ViewAs.create_link!(@resource, session) }
  rescue DurianOs::ViewAs::Refused => e
    render json: { error: e.code }, status: :unprocessable_entity
  end

  private

  def set_resource
    @resource = User.find(params[:id])
  end

  # Only a person who is a member of ORM's account; never a service or emergency account.
  def view_as_refusal
    return 'not_linkable' if @resource.durian_os_protected?

    'not_a_member' unless @resource.account_users.exists?(account_id: DurianOs::Config.account_id)
  end
end
