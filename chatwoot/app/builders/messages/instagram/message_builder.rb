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
    existing = conv.additional_attributes || {}
    return if existing['ig_thread_id'].present?

    url = "#{base_uri}/me/conversations?user_id=#{message_source_id}&access_token=#{@inbox.channel.access_token}"
    response = HTTParty.get(url, timeout: 5)
    return unless response.success?

    ig_conversation_id = JSON.parse(response.body).dig('data', 0, 'id')
    return if ig_conversation_id.blank?

    attrs = existing.merge('ig_conversation_id' => ig_conversation_id)
    decoded = begin
      Base64.decode64(ig_conversation_id)
    rescue StandardError
      nil
    end
    if decoded&.start_with?('ig_dm:')
      thread_id = decoded.delete_prefix('ig_dm:').strip
      attrs['ig_thread_id'] = thread_id if thread_id.match?(/\A\d+\z/)
    end

    conv.update!(additional_attributes: attrs)
  rescue StandardError => e
    Rails.logger.warn("[InstagramThreadIdFetch] conv #{@message&.conversation_id}: #{e.message}")
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
