# Thin proxy from Chatwoot UI to the local zoho-bridge sidecar. Lets the
# dashboard call bridge-only endpoints (like the ticket-dedup decision)
# without exposing the bridge to the public internet — every call here is
# authenticated as a Chatwoot agent first, then forwarded over loopback to
# the sidecar's localhost-only port.
class Api::V1::Accounts::Integrations::ZohoBridgeController < Api::V1::Accounts::BaseController
  before_action :check_authorization

  # POST /api/v1/accounts/:account_id/integrations/zoho_bridge/resolve_ticket_decision
  #   body: { conversation_id, choice, target_ticket_id }
  def resolve_ticket_decision
    response = HTTParty.post(
      "#{bridge_url}/chatwoot/resolve-ticket-decision",
      body:    request.raw_post,
      headers: { 'Content-Type' => 'application/json' },
      timeout: 20
    )
    render json: response.parsed_response, status: proxy_status(response.code)
  rescue StandardError => e
    Rails.logger.error("[zoho-bridge proxy] resolve_ticket_decision failed: #{e.message}")
    render json: { error: 'bridge unavailable', detail: e.message }, status: :bad_gateway
  end

  private

  def bridge_url
    ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')
  end

  # Pass the bridge's status through verbatim for 2xx/4xx; surface 5xx as
  # 502 so the dashboard can show the dedicated "bridge unavailable" toast
  # instead of bubbling a generic server-error page.
  def proxy_status(code)
    code.to_i >= 500 ? :bad_gateway : code.to_i
  end
end
