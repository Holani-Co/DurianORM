class Messages::Instagram::MessageBuilder < Messages::Instagram::BaseMessageBuilder
  def initialize(messaging, inbox, outgoing_echo: false)
    super(messaging, inbox, outgoing_echo: outgoing_echo)
  end

  private

  def find_conversation_scope
    Conversation.where(conversation_params)
                .where("additional_attributes ->> 'type' IS NULL OR additional_attributes ->> 'type' NOT IN ('instagram_comment', 'facebook_comment')")
  end

  def build_message
    super
    ensure_dm_thread_id
  end

  # Resolve and persist the Instagram DM thread id so the dashboard's
  # "Open in Platform" button can deep-link straight to the thread
  # (https://www.instagram.com/direct/t/<thread_id>/) instead of just the
  # contact's profile.
  #
  # The Graph API conversations endpoint returns an opaque conversation id
  # that base64-decodes to "ig_dm:<thread_id>" — that numeric thread id is
  # what instagram.com's web inbox uses in its /direct/t/ URLs. We store
  # both the raw id and (when the decode yields a clean numeric id) the
  # thread id on conversation.additional_attributes.
  #
  # Runs AFTER every message build rather than only at conversation
  # creation so conversations that existed before this feature self-heal
  # on their next incoming message. Skips the HTTP call once the thread id
  # is stored. Best-effort by design: any failure is logged and swallowed —
  # this runs inside perform's transaction, and a raise here would roll
  # back the message itself.
  def ensure_dm_thread_id
    # Anchor on the message that was actually built — NOT the builder's
    # `conversation` getter, which lazily find-OR-CREATEs and could spawn a
    # fresh conversation on the paths where build_message returned early
    # (duplicate webhook, unsupported-files-only message).
    return if @message.blank?

    conv = @message.conversation
    attrs = conv.additional_attributes || {}
    return if attrs['ig_thread_id'].present?

    # Reuse a previously-stored conversation id when the earlier decode
    # failed — lets conversations that captured the raw id before this
    # parser existed derive their thread id WITHOUT another Graph call.
    ig_conversation_id = attrs['ig_conversation_id'].presence || fetch_ig_conversation_id
    return if ig_conversation_id.blank?

    new_attrs = attrs.merge('ig_conversation_id' => ig_conversation_id)
    thread_id = extract_dm_thread_id(ig_conversation_id)
    new_attrs['ig_thread_id'] = thread_id if thread_id

    conv.update!(additional_attributes: new_attrs) unless new_attrs == attrs
  rescue StandardError => e
    Rails.logger.warn("[InstagramThreadIdFetch] conv #{@message&.conversation_id}: #{e.message}")
  end

  def fetch_ig_conversation_id
    url = "#{base_uri}/me/conversations?user_id=#{message_source_id}&access_token=#{@inbox.channel.access_token}"
    response = HTTParty.get(url, timeout: 5)
    return unless response.success?

    JSON.parse(response.body).dig('data', 0, 'id')
  end

  # Extract the numeric DM thread id from a Graph API conversation id.
  #
  # Canonically the id is base64("ig_dm:<thread_id>") — but Meta's newer
  # ids inject extra characters into the prefix (observed in production:
  # "aWdfZAG06..." where clean base64 of "ig_dm:" is "aWdfZG06" — note the
  # extra 'A'), which breaks whole-string decoding. The digits block at the
  # END of the id is still clean base64, though: base64 works in 4-char
  # units, so we scan every 4-aligned SUFFIX (longest first) and return the
  # first one that decodes to a pure 10-50 digit string — that's the thread
  # id instagram.com/direct/t/ expects. Handles both the canonical and the
  # mangled prefix without caring what Meta prepends. Returns nil when no
  # suffix decodes to digits (behaviour then falls back to the profile
  # link, same as before).
  def extract_dm_thread_id(encoded)
    s = encoded.to_s.delete('=')
    (0...s.length).each do |start|
      chunk = s[start..]
      next unless (chunk.length % 4).zero?

      decoded = begin
        Base64.decode64(chunk)
      rescue StandardError
        next
      end
      return decoded if decoded.match?(/\A\d{10,50}\z/)
    end
    nil
  end

  def get_story_object_from_source_id(source_id)
    url = "#{base_uri}/#{source_id}?fields=story,from&access_token=#{@inbox.channel.access_token}"

    response = HTTParty.get(url)

    return JSON.parse(response.body).with_indifferent_access if response.success?

    # Create message first if it doesn't exist
    @message ||= conversation.messages.create!(message_params)
    handle_error_response(response)
    nil
  end

  def handle_error_response(response)
    parsed_response = JSON.parse(response.body)
    error_code = parsed_response.dig('error', 'code')

    # https://developers.facebook.com/docs/messenger-platform/error-codes
    # Access token has expired or become invalid.
    channel.authorization_error! if error_code == 190

    # There was a problem scraping data from the provided link.
    # https://developers.facebook.com/docs/graph-api/guides/error-handling/ search for error code 1609005
    if error_code == 1_609_005
      @message.attachments.destroy_all
      @message.update(content: I18n.t('conversations.messages.instagram_deleted_story_content'))
    end

    Rails.logger.error("[InstagramStoryFetchError]: #{parsed_response.dig('error', 'message')} #{error_code}")
  end

  def base_uri
    "https://graph.instagram.com/#{GlobalConfigService.load('INSTAGRAM_API_VERSION', 'v22.0')}"
  end
end
