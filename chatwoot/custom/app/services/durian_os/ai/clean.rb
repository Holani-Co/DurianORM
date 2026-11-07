# What an AI answer may carry (docs/mcp/MCP.md, section 5, "The answer back to the AI"):
# - no session or cookie headers;
# - no secret, at any depth: every key named in SECRET_KEYS is removed;
# - no file: a string holding a signed file link becomes FILE_NOTE (a file's name, type and size still reach the AI).
module DurianOs::Ai::Clean
  SECRET_KEYS = %w[
    access_token secret hmac_token auth_token account_sid api_key_sid api_key_secret imap_password smtp_password
    provider_config pubsub_token password auth_config client_secret refresh_token verify_token webhook_secret
    app_secret api_key
  ].to_set.freeze
  FILE_LINK = %r{/rails/active_storage/|X-Amz-Signature=|X-Goog-Signature=}i
  FILE_NOTE = '(file: open it in ORM)'.freeze
  DROP_HEADERS = %w[access-token client uid expiry token-type authorization set-cookie].freeze

  module_function

  def json(value)
    case value
    when Hash
      value.each_with_object({}) do |(key, item), clean|
        clean[key] = json(item) unless SECRET_KEYS.include?(key.to_s.downcase)
      end
    when Array then value.map { |item| json(item) }
    when String then FILE_LINK.match?(value) ? FILE_NOTE : value
    else value
    end
  end

  # The response headers without session tokens or cookies (Rack 3 headers are lowercase; older ones may not be).
  def headers(headers)
    headers.to_h.reject { |name, _| DROP_HEADERS.include?(name.to_s.downcase) }
  end

  def header(headers, name)
    headers.to_h.find { |key, _| key.to_s.casecmp?(name) }&.last
  end
end
