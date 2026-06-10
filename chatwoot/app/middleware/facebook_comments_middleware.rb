# frozen_string_literal: true

# Rack middleware that intercepts POST /bot requests and extracts
# Facebook Page feed comment events before the facebook-messenger gem
# processes the payload (the gem only handles messaging/DM events).
#
# It also SHIELDS the gem from feed payloads: the gem's Server assumes
# every entry carries `messaging`/`standby` and calls `.each` on it, so a
# feed-only delivery raises NoMethodError → 500 → Meta retries and, after
# enough failures, marks the whole webhook subscription unhealthy and
# stops delivering (which silently kills Messenger too, not just
# comments). Feed-only payloads are ACKed here with a 200 and never reach
# the gem.
#
# Security: events are only enqueued after verifying Meta's
# X-Hub-Signature (HMAC-SHA1 of the raw body — the same scheme the gem
# checks). This middleware runs BEFORE the gem's own integrity check, and
# without a check of our own, a forged POST /bot could create fake
# conversations.
class FacebookCommentsMiddleware
  def initialize(app)
    @app = app
  end

  def call(env)
    return @app.call(env) unless post_to_bot?(env)

    body = read_body(env)
    payload = safe_parse(body)

    # Only Page-object payloads concern us; anything else passes straight
    # through to the gem untouched (including unparseable bodies — the gem
    # owns rejecting those).
    return @app.call(env) unless payload.is_a?(Hash) && payload['object'] == 'page'

    process_feed_comments(payload, env, body)

    if messaging_entries?(payload)
      shielded_delegate(env)
    else
      # Feed-only delivery — the gem would 500 on it (see class comment).
      # We've enqueued what we needed; ACK so Meta doesn't retry.
      [200, { 'Content-Type' => 'text/plain' }, ['OK']]
    end
  end

  private

  def post_to_bot?(env)
    env['REQUEST_METHOD'] == 'POST' && env['PATH_INFO']&.start_with?('/bot')
  end

  def read_body(env)
    body = env['rack.input'].read
    env['rack.input'] = StringIO.new(body) # rewind for downstream gem
    body
  rescue StandardError
    ''
  end

  def safe_parse(body)
    JSON.parse(body)
  rescue JSON::ParserError, TypeError
    # NOTE: a previous version rescued JSON::ParseError — a constant that
    # doesn't exist — so malformed JSON raised NameError mid-rescue.
    nil
  end

  def messaging_entries?(payload)
    entries = payload['entry']
    return false unless entries.is_a?(Array)

    entries.any? { |e| e.is_a?(Hash) && (e['messaging'] || e['standby']) }
  end

  # Mixed batches (messaging + changes entries in one delivery) still go to
  # the gem for the messaging part; if it trips on a changes entry, the
  # Bot.on handlers for entries processed before the crash already fired
  # and our feed events are already enqueued — so a 200 is the honest
  # response, and it stops Meta's retry storm.
  def shielded_delegate(env)
    @app.call(env)
  rescue NoMethodError => e
    Rails.logger.warn("FacebookCommentsMiddleware: gem failed on mixed payload: #{e.message}")
    [200, { 'Content-Type' => 'text/plain' }, ['OK']]
  end

  def process_feed_comments(payload, env, body)
    payload['entry'].each do |entry|
      next unless entry.is_a?(Hash)

      page_id = entry['id']
      changes = entry['changes']
      next if page_id.blank? || !changes.is_a?(Array)
      next unless valid_signature?(env, body, page_id)

      changes.each do |change|
        next unless change.is_a?(Hash) && change['field'] == 'feed'

        value = change['value'] || {}
        next unless value['item'] == 'comment' && value['verb'] == 'add'

        Rails.logger.info("FacebookCommentsMiddleware: comment on page #{page_id}")
        Webhooks::FacebookCommentJob.perform_later(page_id, value)
      end
    end
  rescue StandardError => e
    # Never let feed handling break Messenger delivery.
    Rails.logger.error("FacebookCommentsMiddleware error: #{e.message}")
  end

  # Same scheme the facebook-messenger gem verifies: HMAC-SHA1 of the raw
  # body with the app secret, resolved per-page through the configured
  # provider (channel-specific secret, falling back to FB_APP_SECRET).
  def valid_signature?(env, body, page_id)
    header = env['HTTP_X_HUB_SIGNATURE'].to_s
    return false unless header.start_with?('sha1=')

    secret = Facebook::Messenger.config.provider.app_secret_for(page_id)
    return false if secret.blank?

    expected = "sha1=#{OpenSSL::HMAC.hexdigest('sha1', secret, body)}"
    ActiveSupport::SecurityUtils.secure_compare(expected, header)
  rescue StandardError => e
    Rails.logger.warn("FacebookCommentsMiddleware: signature check error: #{e.message}")
    false
  end
end
