# Public entry point for Zoho CRM's "Deal stage changed" Workflow webhook.
# The zoho-bridge only listens on loopback, so Zoho reaches it through here: the
# request (body, content type and query string — including the ?key= secret) is
# forwarded unchanged to the bridge, which checks the secret, reads the deal from
# Zoho and sends any matching WhatsApp template (Settings → CRM WhatsApp Automation).
class Webhooks::CrmStageController < ActionController::API
  def process_payload
    response = HTTParty.post(
      "#{ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')}/crm/stage-changed",
      query: request.query_parameters,
      body: request.raw_post,
      headers: { 'Content-Type' => request.content_type.presence || 'application/json' },
      timeout: 15
    )
    render json: response.parsed_response, status: response.code.to_i >= 500 ? :bad_gateway : response.code.to_i
  rescue StandardError => e
    Rails.logger.error("[crm-stage webhook] bridge unreachable: #{e.message}")
    render json: { error: 'bridge unavailable' }, status: :bad_gateway
  end
end
