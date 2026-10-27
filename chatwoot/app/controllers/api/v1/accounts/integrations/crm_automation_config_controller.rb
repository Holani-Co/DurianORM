# Admin-only proxy from the dashboard to the zoho-bridge CRM automation API
# (Settings → CRM WhatsApp Automation): the rules that send a WhatsApp template
# when a Zoho deal reaches a stage, the sales team phone list, the run log, a
# per-deal preview and a test send. Same contract as the other settings proxies:
# authenticated as a Chatwoot admin here, forwarded over loopback with the
# bridge's shared secret, the acting admin's email stamped onto writes.
class Api::V1::Accounts::Integrations::CrmAutomationConfigController < Api::V1::Accounts::BaseController
  before_action :check_admin_authorization

  def show
    proxy_get('/admin/crm-automation')
  end

  def versions
    proxy_get('/admin/crm-automation/versions')
  end

  def version
    proxy_get("/admin/crm-automation/versions/#{params[:version_id].to_i}")
  end

  def validate
    proxy_post('/admin/crm-automation/validate', request.raw_post)
  end

  def publish
    proxy_post('/admin/crm-automation/publish', body_with_actor)
  end

  def rollback
    proxy_post('/admin/crm-automation/rollback', body_with_actor)
  end

  # POST .../crm_automation_config/preview  body: { deal_id } — nothing is sent
  def preview
    proxy_post('/admin/crm-automation/preview', request.raw_post, 45)
  end

  # POST .../crm_automation_config/test_send  body: { rule_id, deal_id, phone }
  def test_send
    proxy_post('/admin/crm-automation/test-send', body_with_actor, 45)
  end

  private

  def check_admin_authorization
    raise Pundit::NotAuthorizedError unless Current.account_user&.administrator?
  end

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
    response = HTTParty.get("#{bridge_url}#{path}", headers: bridge_headers, timeout: 30)
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
    { 'Content-Type' => 'application/json', 'X-Routing-Admin-Secret' => ENV.fetch('ROUTING_ADMIN_SECRET', '') }
  end

  def bridge_url
    ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')
  end

  def bridge_error(path, error)
    Rails.logger.error("[crm-automation proxy] #{path} failed: #{error.message}")
    render json: { error: 'bridge unavailable', detail: error.message }, status: :bad_gateway
  end

  def proxy_status(code)
    code.to_i >= 500 ? :bad_gateway : code.to_i
  end
end
