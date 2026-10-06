# Admin-only proxy from the Chatwoot dashboard to the zoho-bridge stores-config
# API. The bridge owns the store/showroom registry (data/store_registry.json +
# a UI-editable override layer); this lets an account ADMINISTRATOR view and edit
# the store details sent to customers from Settings, without exposing the bridge.
#
# Every call is authenticated as a Chatwoot admin here, then forwarded over
# loopback to the sidecar with the shared secret the bridge requires. The acting
# admin's email is stamped onto writes for the bridge audit log.
class Api::V1::Accounts::Integrations::StoresConfigController < Api::V1::Accounts::BaseController
  before_action :check_admin_authorization

  # GET /api/v1/accounts/:account_id/integrations/stores_config
  def show
    proxy_get('/admin/stores-config')
  end

  # GET .../stores_config/versions
  def versions
    proxy_get('/admin/stores-config/versions')
  end

  # GET .../stores_config/version?version_id=N
  def version
    proxy_get("/admin/stores-config/versions/#{params[:version_id].to_i}")
  end

  # POST .../stores_config/validate   body: { doc }
  def validate
    proxy_post('/admin/stores-config/validate', request.raw_post)
  end

  # POST .../stores_config/publish    body: { doc, note }
  def publish
    proxy_post('/admin/stores-config/publish', body_with_actor)
  end

  # POST .../stores_config/rollback   body: { version_id }
  def rollback
    proxy_post('/admin/stores-config/rollback', body_with_actor)
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
    Rails.logger.error("[stores-config proxy] #{path} failed: #{error.message}")
    render json: { error: 'bridge unavailable', detail: error.message }, status: :bad_gateway
  end

  # Pass the bridge's status through for 2xx/4xx; surface 5xx as 502 so the
  # dashboard shows the "bridge unavailable" state instead of a server-error page.
  def proxy_status(code)
    code.to_i >= 500 ? :bad_gateway : code.to_i
  end
end
