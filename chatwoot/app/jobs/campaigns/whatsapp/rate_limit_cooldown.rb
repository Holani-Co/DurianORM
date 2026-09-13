# Lightweight, per-inbox circuit breaker for campaign fan-out. When Meta returns
# a rate-limit / throughput response on a send, the delivery job "trips" a short
# cooldown; the batch job checks it and pauses enqueuing new sends until it clears.
# Backed by Rails.cache (Redis) and fails open — if the cache is unavailable the
# fan-out simply proceeds as before.
module Campaigns::Whatsapp::RateLimitCooldown
  KEY_PREFIX = 'whatsapp_campaign_cooldown:inbox:'.freeze

  module_function

  def key(inbox_id)
    "#{KEY_PREFIX}#{inbox_id}"
  end

  def cooling_down?(inbox_id)
    Rails.cache.read(key(inbox_id)).present?
  rescue StandardError
    false
  end

  def trip!(inbox_id, ttl)
    Rails.cache.write(key(inbox_id), true, expires_in: ttl)
  rescue StandardError => e
    Rails.logger.warn("[whatsapp] failed to set campaign cooldown for inbox #{inbox_id}: #{e.message}")
  end
end
