# Admin-only proxy from the Chatwoot dashboard to the zoho-bridge routing-config
# API. The bridge owns the email routing rules (routing_rules.yaml + a UI-editable
# override layer); this lets an account ADMINISTRATOR view and publish that
# override from Settings without exposing the bridge to the public internet.
#
# Every call is authenticated as a Chatwoot admin here, then forwarded over
# loopback to the sidecar with the shared secret the bridge requires. The acting
# admin's email is stamped onto writes so the bridge audit log records WHO changed
# what.
class Api::V1::Accounts::Integrations::RoutingConfigController < Api::V1::Accounts::BaseController
  before_action :check_admin_authorization

  # GET /api/v1/accounts/:account_id/integrations/routing_config
  def show
    proxy_get('/admin/routing-config')
  end

  # GET .../routing_config/versions
  def versions
    proxy_get('/admin/routing-config/versions')
  end

  # GET .../routing_config/version?version_id=N
  def version
    proxy_get("/admin/routing-config/versions/#{params[:version_id].to_i}")
  end

  # POST .../routing_config/validate   body: { doc }
  def validate
    proxy_post('/admin/routing-config/validate', request.raw_post)
  end

  # POST .../routing_config/publish    body: { doc, note }
  def publish
    proxy_post('/admin/routing-config/publish', body_with_actor)
  end

  # POST .../routing_config/rollback   body: { version_id }
  def rollback
    proxy_post('/admin/routing-config/rollback', body_with_actor)
  end

  # POST .../routing_config/preview    body: { doc, subject, body, sender_email }
  def preview
    proxy_post('/admin/routing-config/preview', request.raw_post)
  end

  private

  def check_admin_authorization
    raise Pundit::NotAuthorizedError unless Current.account_user&.administrator?
  end

  # Stamp the acting admin's email so the bridge audit records who published.
  def body_with_actor
    payload = begin
      JSON.parse(request.raw_post.presence || '{}')
    rescue JSON::ParserError
      {}
    end
    payload['actor'] = Current.user&.email
    payload.to_json
  end

  def proxy_get(path)
    response = HTTParty.get("#{bridge_url}#{path}", headers: bridge_headers, timeout: 20)
    render json: response.parsed_response, status: proxy_status(response.code)
  rescue StandardError => e
    bridge_error(path, e)
  end

  def proxy_post(path, body)
    response = HTTParty.post("#{bridge_url}#{path}", body: body, headers: bridge_headers, timeout: 30)
    render json: response.parsed_response, status: proxy_status(response.code)
  rescue StandardError => e
    bridge_error(path, e)
  end

  def bridge_headers
    {
      'Content-Type' => 'application/json',
      'X-Routing-Admin-Secret' => ENV.fetch('ROUTING_ADMIN_SECRET', '')
    }
  end

  def bridge_url
    ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')
  end

  def bridge_error(path, error)
    Rails.logger.error("[routing-config proxy] #{path} failed: #{error.message}")
    render json: { error: 'bridge unavailable', detail: error.message }, status: :bad_gateway
  end

  # Pass the bridge's status through for 2xx/4xx; surface 5xx as 502 so the
  # dashboard shows the "bridge unavailable" state instead of a server-error page.
  def proxy_status(code)
    code.to_i >= 500 ? :bad_gateway : code.to_i
  end
end
