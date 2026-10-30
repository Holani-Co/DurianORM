# Durian OS "View as" in every controller (shared View as spec 3 and 5):
# - durian_os_view_as: the View as of the session token in use, or nil (also a view helper);
# - the actions in DurianOs::ViewAs::BLOCKED_ACTIONS answer 403 while viewing as someone;
# - while viewing, DurianOs::Current.view_as is set (audit rows name the admin) and each
#   request is logged with the admin.
module Custom::Concerns::ApplicationControllerConcern
  extend ActiveSupport::Concern

  included do
    helper_method :durian_os_view_as
    before_action :durian_os_refuse_while_viewing_as
    around_action :durian_os_with_view_as, unless: :devise_controller?
  end

  private

  def durian_os_view_as
    return @durian_os_view_as if @durian_os_view_as

    client = @token.respond_to?(:client) ? @token.client : nil
    @durian_os_view_as = DurianOs::ViewAs.session_for(durian_os_view_as_user, client)
  end

  def durian_os_view_as_user
    @durian_os_view_as_user || [@resource, @user].find { |candidate| candidate.is_a?(User) }
  end

  def durian_os_refuse_while_viewing_as
    return unless DurianOs::ViewAs.blocked_action?(controller_path, action_name)

    # Devise controllers have not read the session token yet (and Devise's own controllers
    # cannot run DeviseTokenAuth's set_user_by_token), so read it from the headers here.
    durian_os_read_session_token if devise_controller?
    view_as = durian_os_view_as
    return if view_as.nil?

    DurianOs::ViewAsLog.blocked(view_as, durian_os_view_as_user, request)
    render json: { error: DurianOs::ViewAs::BLOCKED_MESSAGE }, status: :forbidden
  end

  def durian_os_with_view_as
    view_as = durian_os_view_as
    return yield if view_as.nil?

    DurianOs::Current.set(view_as: view_as, user: durian_os_view_as_user) do
      DurianOs::ViewAsLog.request(view_as, durian_os_view_as_user, request)
      yield
    end
  end

  def durian_os_read_session_token
    uid, token, client = durian_os_session_headers.values_at('uid', 'access-token', 'client')
    return if uid.blank? || token.blank? || client.blank?

    user = User.find_by(uid: uid)
    return unless user&.valid_token?(token, client)

    @durian_os_view_as_user = user
    @durian_os_view_as = DurianOs::ViewAs.session_for(user, client)
  end

  def durian_os_session_headers
    headers = %w[uid access-token client].index_with { |name| request.headers[name] }
    return headers if headers['access-token'].present?

    bearer = request.headers['Authorization'].to_s[/\ABearer (.+)\z/, 1]
    bearer ? JSON.parse(Base64.strict_decode64(bearer)).slice('uid', 'access-token', 'client') : headers
  rescue ArgumentError, JSON::ParserError
    headers
  end
end
