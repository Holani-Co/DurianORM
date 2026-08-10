# Durian — Follow-ups report + its label configuration.
#
# The client marks conversations that need following up with their own labels.
# An admin picks which labels count as "follow-up" (stored on the account, so
# it's managed entirely from Chatwoot — no code change when a new label is
# added), and this reports the backlog per label with drill-through.
class Api::V1::Accounts::FollowUpReportsController < Api::V1::Accounts::BaseController
  before_action :check_authorization

  CONFIG_KEY = 'follow_up_report_labels'.freeze

  # GET — the configured labels + per-label counts for the selected period.
  def show
    render json: { selected: selected_labels, rows: rows }
  end

  # PATCH — save the admin's chosen follow-up labels onto the account.
  def update
    labels = Array(params[:labels]).map(&:to_s).reject(&:blank?).uniq
    Current.account.update!(
      custom_attributes: Current.account.custom_attributes.merge(CONFIG_KEY => labels)
    )
    render json: { selected: labels }
  end

  private

  def selected_labels
    Current.account.custom_attributes[CONFIG_KEY] || []
  end

  # For each follow-up label: the OPEN backlog (currently tagged + open, a "now"
  # count) and the total tagged in the selected period.
  def rows
    selected_labels.map do |title|
      { label: title, open: tag_count(title, only_open: true), total: tag_count(title, in_range: true) }
    end
  end

  def tag_count(title, only_open: false, in_range: false)
    conversation_scope = { account_id: Current.account.id }
    conversation_scope[:status] = Conversation.statuses[:open] if only_open
    conversation_scope[:created_at] = range if in_range
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN conversations ON taggings.taggable_id = conversations.id')
      .joins('INNER JOIN tags ON tags.id = taggings.tag_id')
      .where(taggable_type: 'Conversation', context: 'labels')
      .where(tags: { name: title })
      .where(conversations: conversation_scope)
      .count
  end

  def range
    since = Time.zone.at((params[:since].presence || 30.days.ago.to_i).to_i)
    till  = Time.zone.at((params[:until].presence || Time.current.to_i).to_i)
    since..till
  end

  def check_authorization
    authorize :report, :view?
  end
end
