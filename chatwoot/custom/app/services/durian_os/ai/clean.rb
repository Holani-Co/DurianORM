# What an AI answer may carry (docs/mcp/MCP.md, section 5, "The answer back to the AI"):
# - no session or cookie headers;
# - no secret, at any depth: every key named in SECRET_KEYS is removed (a blob's signed id opens its file for good);
# - no file link, decided by key first: a string under a key named in FILE_KEYS becomes FILE_NOTE whatever its host.
#   An incoming Instagram attachment, one never downloaded, a story and an ad carry the provider's CDN link there,
#   and it opens with no sign-in. Then by pattern, anywhere: a string holding an ORM file link or a signed or CDN
#   link (FILE_LINK) becomes FILE_NOTE. A file's name, type and size still reach the AI; a location keeps its
#   coordinates.
module DurianOs::Ai::Clean
  SECRET_KEYS = %w[
    access_token secret hmac_token auth_token account_sid api_key_sid api_key_secret imap_password smtp_password
    provider_config pubsub_token password auth_config client_secret refresh_token verify_token webhook_secret
    app_secret api_key signed_id media_signed_id
  ].to_set.freeze
  FILE_KEYS = %w[
    data_url thumb_url external_url download_url file_url media_url story_url image_url video_url audio_url
    thumbnail_url thumbnail avatar_url
  ].to_set.freeze
  FILE_LINK = %r{/rails/active_storage/|X-Amz-Signature=|X-Goog-Signature=|fbcdn\.net|fbsbx\.com|cdninstagram\.com}i
  FILE_NOTE = '(file: open it in ORM)'.freeze
  DROP_HEADERS = %w[access-token client uid expiry token-type authorization set-cookie].freeze

  module_function

  # `key` is the (lowercased) key the value sits under, so a list under a file key is cleaned like a single link.
  def json(value, key = nil)
    case value
    when Hash then json_object(value)
    when Array then value.map { |item| json(item, key) }
    when String then file?(value, key) ? FILE_NOTE : value
    else value
    end
  end

  def json_object(value)
    value.each_with_object({}) do |(name, item), clean|
      name_key = name.to_s.downcase
      clean[name] = json(item, name_key) unless SECRET_KEYS.include?(name_key)
    end
  end

  def file?(text, key)
    (FILE_KEYS.include?(key) && text.present?) || FILE_LINK.match?(text)
  end

  # The response headers without session tokens or cookies (Rack 3 headers are lowercase; older ones may not be).
  def headers(headers)
    headers.to_h.reject { |name, _| DROP_HEADERS.include?(name.to_s.downcase) }
  end

  def header(headers, name)
    headers.to_h.find { |key, _| key.to_s.casecmp?(name) }&.last
  end
end
