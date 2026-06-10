# frozen_string_literal: true

# Processes an incoming Facebook Page comment and creates a Chatwoot
# conversation/message for it.
#
# Facebook feed comment value shape:
# {
#   "from"         => { "id" => "USER_ID", "name" => "User Name" },
#   "comment_id"   => "COMMENT_ID",
#   "post_id"      => "PAGE_ID_POST_ID",
#   "parent_id"    => "PARENT_COMMENT_OR_POST_ID",
#   "verb"         => "add",
#   "item"         => "comment",
#   "created_time" => 1234567890,
#   "message"      => "Comment text"
# }
class Facebook::CommentService
  def initialize(page_id:, value:)
    @page_id = page_id
    @value   = value.with_indifferent_access
  end

  def perform
    return unless channel && inbox
    # Skip comments made by the page itself (our own replies echoed back)
    return if sender_id.to_s == @page_id.to_s

    ActiveRecord::Base.transaction do
      build_contact_inbox
      build_message
    end
    apply_comment_label
  rescue StandardError => e
    ChatwootExceptionTracker.new(e).capture_exception
  end

  private

  def channel
    @channel ||= Channel::FacebookPage.find_by(page_id: @page_id)
  end

  def inbox
    @inbox ||= channel&.inbox
  end

  def sender_id
    @value.dig('from', 'id')
  end

  def sender_name
    @value.dig('from', 'name') || 'Facebook User'
  end

  def post_id
    @value['post_id']
  end

  def comment_id
    @value['comment_id']
  end

  def message_text
    @value['message']
  end

  def build_contact_inbox
    @contact_inbox = ::ContactInboxWithContactBuilder.new(
      source_id: sender_id,
      inbox: inbox,
      contact_attributes: contact_attributes
    ).perform
  end

  def contact_attributes
    base = { name: sender_name, account_id: inbox.account_id }
    base.merge(fetch_facebook_profile)
  rescue StandardError
    base
  end

  def fetch_facebook_profile
    api = Koala::Facebook::API.new(channel.page_access_token)
    result = api.get_object(sender_id) || {}
    {
      name: "#{result['first_name'] || sender_name} #{result['last_name'] || ''}".strip,
      avatar_url: result['profile_pic']
    }
  rescue Koala::Facebook::AuthenticationError, Koala::Facebook::ClientError => e
    Rails.logger.warn("Facebook profile fetch failed for #{sender_id}: #{e.message}")
    {}
  end

  def build_message
    @message = conversation.messages.find_or_create_by!(source_id: comment_id) do |m|
      m.account_id   = conversation.account_id
      m.inbox_id     = conversation.inbox_id
      m.message_type = :incoming
      m.content      = message_text
      m.sender       = @contact_inbox.contact
      m.content_attributes = { item_type: 'facebook_comment', post_id: post_id }
    end
  end

  def conversation
    @conversation ||= find_or_create_conversation
  end

  def find_or_create_conversation
    # Group all comments on the same post into one conversation.
    # Scope to facebook_comment type only — never find/modify a DM conversation.
    existing = Conversation.where(
      account_id: inbox.account_id,
      inbox_id: inbox.id,
      contact_id: @contact_inbox.contact_id
    ).where("additional_attributes->>'type' = 'facebook_comment'")
                           .where("additional_attributes->>'post_id' = ?", post_id).first

    existing || Conversation.create!(
      account_id: inbox.account_id,
      inbox_id: inbox.id,
      contact_id: @contact_inbox.contact_id,
      contact_inbox_id: @contact_inbox.id,
      additional_attributes: comment_conversation_attributes
    )
  end

  # additional_attributes for a NEW comment conversation. The permalink
  # feeds the dashboard's "Open Facebook post" button — usePlatformLink's
  # facebook_comment branch reads additional_attributes.permalink_url and
  # falls back to a generic page link without it (parity with the
  # Instagram comment service, which stores the post permalink the same
  # way).
  def comment_conversation_attributes
    attrs = { post_id: post_id, type: 'facebook_comment' }
    permalink = fetch_post_permalink
    attrs[:permalink_url] = permalink if permalink.present?
    attrs
  end

  # Resolve the post's public URL via Graph. Best-effort: nil on any
  # failure (network, token missing pages_read_user_content) so a Graph
  # hiccup never blocks comment ingestion.
  def fetch_post_permalink
    return if post_id.blank?

    token = channel.page_access_token
    return if token.blank?

    response = HTTParty.get(
      "https://graph.facebook.com/v22.0/#{post_id}",
      query: { fields: 'permalink_url', access_token: token }
    )
    return unless response.success?

    response.parsed_response['permalink_url'].presence
  rescue StandardError => e
    Rails.logger.warn("Facebook::CommentService permalink fetch failed for post #{post_id}: #{e.message}")
    nil
  end

  def apply_comment_label
    return unless @conversation

    # Safety: never apply comment label to a DM conversation.
    conv_type = @conversation.additional_attributes&.dig('type').to_s
    return if conv_type.present? && !conv_type.include?('comment')

    ensure_label_exists('comment', '#e84393')
    @conversation.add_labels(['comment'])
  rescue StandardError => e
    Rails.logger.warn("Could not apply comment label: #{e.message}")
  end

  def ensure_label_exists(title, color)
    Label.find_or_create_by!(account_id: inbox.account_id, title: title) do |l|
      l.color           = color
      l.description     = 'Post comment (auto-tagged)'
      l.show_on_sidebar = true
    end
  end
end
