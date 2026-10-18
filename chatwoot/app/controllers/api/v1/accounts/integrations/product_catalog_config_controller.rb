# Admin-only proxy from the Chatwoot dashboard to the zoho-bridge product
# catalog API (Settings → Product Catalog). The bridge owns the price list the
# social agent uses — published from a link-shared Google Sheet or an uploaded
# file, versioned with rollback — and this lets an account ADMINISTRATOR view
# it, update it and roll it back without a developer or a redeploy.
#
# Same contract as the stores/agent-prompts proxies: authenticated as a Chatwoot
# admin here, forwarded over loopback with the bridge's shared secret, and the
# acting admin's email stamped onto writes for the bridge audit log.
class Api::V1::Accounts::Integrations::ProductCatalogConfigController < Api::V1::Accounts::BaseController
  before_action :check_admin_authorization

  # Fetching a Google Sheet and parsing a large workbook take longer than a
  # plain config save.
  IMPORT_TIMEOUT = 90

  # GET /api/v1/accounts/:account_id/integrations/product_catalog_config
  def show
    proxy_get('/admin/catalog-config')
  end

  # GET .../product_catalog_config/versions
  def versions
    proxy_get('/admin/catalog-config/versions')
  end

  # GET .../product_catalog_config/version?version_id=N
  def version
    proxy_get("/admin/catalog-config/versions/#{params[:version_id].to_i}")
  end

  # GET .../product_catalog_config/export — the live list as template CSV
  def export
    proxy_get('/admin/catalog-config/export')
  end

  # POST .../product_catalog_config/preview  body: { file_b64, filename } | { sheet_url }
  def preview
    proxy_post('/admin/catalog-config/preview', request.raw_post, IMPORT_TIMEOUT)
  end

  # POST .../product_catalog_config/publish  body: preview body + { price_period, note, auto_sync }
  def publish
    proxy_post('/admin/catalog-config/publish', body_with_actor, IMPORT_TIMEOUT)
  end

  # POST .../product_catalog_config/sync — re-read the linked Google Sheet now
  def sync
    proxy_post('/admin/catalog-config/sync', body_with_actor, IMPORT_TIMEOUT)
  end

  # POST .../product_catalog_config/sync_settings  body: { auto_sync } | { unlink: true }
  def sync_settings
    proxy_post('/admin/catalog-config/settings', body_with_actor)
  end

  # POST .../product_catalog_config/rollback  body: { version_id }
  def rollback
    proxy_post('/admin/catalog-config/rollback', body_with_actor)
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

  def proxy_post(path, body, timeout = 30)
    response = HTTParty.post("#{bridge_url}#{path}", body: body, headers: bridge_headers, timeout: timeout)
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
    Rails.logger.error("[catalog-config proxy] #{path} failed: #{error.message}")
    render json: { error: 'bridge unavailable', detail: error.message }, status: :bad_gateway
  end

  # Pass the bridge's status through for 2xx/4xx; surface 5xx as 502 so the
  # dashboard shows the "bridge unavailable" state instead of a server-error page.
  def proxy_status(code)
    code.to_i >= 500 ? :bad_gateway : code.to_i
  end
end
