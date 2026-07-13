class Api::V1::Accounts::Conversations::MessagesController < Api::V1::Accounts::Conversations::BaseController
  before_action :ensure_api_inbox, only: :update

  def index
    @messages = message_finder.perform
  end

  def create
    user = Current.user || @resource
    mb = Messages::MessageBuilder.new(user, @conversation, params)
    @message = mb.perform
  rescue StandardError => e
    render_could_not_create_error(e.message)
  end

  def update
    Messages::StatusUpdateService.new(message, permitted_params[:status], permitted_params[:external_error]).perform
    @message = message
  end

  def destroy
    ActiveRecord::Base.transaction do
      message.update!(content: I18n.t('conversations.messages.deleted'), content_type: :text, content_attributes: { deleted: true })
      message.attachments.destroy_all
    end
  end

  # Mark an AI suggestion card (the bridge's interactive reply note) as approved
  # and sent, recording the acting agent. The card transforms into an
  # "Approved & sent by <agent>" line in place — no soft-delete, so there's no
  # "message deleted" tombstone. Restricted to ai_review_suggestion notes.
  def mark_suggestion_sent
    attrs = message.content_attributes || {}
    return render_could_not_create_error('not a suggestion card') unless %w[ai_review_suggestion ai_order_reply].include?(attrs['type'])

    message.update!(content_attributes: attrs.merge('sent' => true,
                                                    'sent_by' => Current.user&.available_name.presence || Current.user&.name))
    # Tag who replied so the reviews inbox can filter "replied by <agent>".
    # Uses a slug of the agent's name (`replied-by-aditya`) — much more
    # readable than the raw id (`replied-by-1`) that shows up as a label chip
    # on the conversation card.
    if Current.user
      slug = replied_by_slug(Current.user)
      message.conversation.add_labels(["replied-by-#{slug}"]) if slug.present?
    end
    @message = message
  end

  # Slugify a User's display name for the `replied-by-<slug>` label. Falls back
  # through name → email local-part → id so we NEVER return an empty slug.
  def replied_by_slug(user)
    raw = user.available_name.presence || user.name.presence || user.email.to_s.split('@').first.to_s
    slug = raw.downcase.gsub(/[^a-z0-9]+/, '-').gsub(/(^-+|-+$)/, '')
    slug.presence || user.id.to_s
  end

  def retry
    return if message.blank?

    service = Messages::StatusUpdateService.new(message, 'sent')
    service.perform
    message.update!(content_attributes: {})
    ::SendReplyJob.perform_later(message.id)
  rescue StandardError => e
    render_could_not_create_error(e.message)
  end

  def translate
    return head :ok if already_translated_content_available?

    translated_content = Integrations::GoogleTranslate::ProcessorService.new(
      message: message,
      target_language: permitted_params[:target_language]
    ).perform

    if translated_content.present?
      translations = {}
      translations[permitted_params[:target_language]] = translated_content
      translations = message.translations.merge!(translations) if message.translations.present?
      message.update!(translations: translations)
    end

    render json: { content: translated_content }
  end

  private

  def message
    @message ||= @conversation.messages.find(permitted_params[:id])
  end

  def message_finder
    @message_finder ||= MessageFinder.new(@conversation, params)
  end

  def permitted_params
    params.permit(:id, :target_language, :status, :external_error)
  end

  def already_translated_content_available?
    message.translations.present? && message.translations[permitted_params[:target_language]].present?
  end

  # API inbox check
  def ensure_api_inbox
    # Only API inboxes can update messages
    render json: { error: 'Message status update is only allowed for API inboxes' }, status: :forbidden unless @conversation.inbox.api?
  end
end
