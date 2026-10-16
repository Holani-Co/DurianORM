# Durian — the email categories that forward to a team, with each one's address,
# read LIVE from the bridge's routing config. Shared by the Forwarded Emails
# report controller (the admin's category picker) and the weekly auto-send job.
# Returns nil when the bridge is unavailable (so callers can distinguish that
# from "no forwarding categories"), else an array of { key:, name:, forward_to: }.
class Reports::ForwardingCategories
  def self.list
    new.list
  end

  def list
    response = HTTParty.get("#{ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')}/admin/routing-config",
                            headers: { 'X-Routing-Admin-Secret' => ENV.fetch('ROUTING_ADMIN_SECRET', '') }, timeout: 20)
    rules = response.parsed_response.dig('effective', 'categories') if response.success?
    return nil unless rules.is_a?(Hash)

    rules.filter_map { |key, rule| forwarding_category(key, rule) }.sort_by { |category| category[:name] }
  rescue StandardError => e
    Rails.logger.error("[forwarded-report] categories fetch failed: #{e.message}")
    nil
  end

  private

  # A category that emails a team: it forwards, or one of its subcategories does.
  # Bulk orders (suppress_forward) and disabled categories never forward.
  def forwarding_category(key, rule)
    return unless forwards?(rule)

    { key: key, name: rule['display_name'].presence || key.humanize, forward_to: rule['forward_to'].to_s.strip }
  end

  def forwards?(rule)
    return false if rule['disabled'] || rule['suppress_forward']

    rule['action'] == 'forward' || (rule['vertical_routing'] || {}).values.any? { |route| route&.dig('forward_to').present? }
  end
end
