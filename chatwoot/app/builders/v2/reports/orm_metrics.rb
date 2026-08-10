# Durian — shared helpers for the ORM report builders (Overview, AI
# Performance, CRM funnel, Reviews). Keeps range parsing and label counting in
# one place so the tiles and their drill-throughs stay consistent.
module V2::Reports::OrmMetrics
  # since/until arrive as epoch seconds (the report filter emits from/to). Falls
  # back to the last 30 days when a bound is missing.
  def range
    @range ||= begin
      since = Time.zone.at((params[:since].presence || 30.days.ago.to_i).to_i)
      till  = Time.zone.at((params[:until].presence || Time.current.to_i).to_i)
      since..till
    end
  end

  # Count of conversations tagged with `label`, scoped to conversations started
  # in the range — or, with only_open, the live count still open (the "now"
  # queue). Matches the label view the matching tile drills into.
  def label_count(label, only_open: false)
    conversation_scope = { account_id: account.id }
    if only_open
      conversation_scope[:status] = Conversation.statuses[:open]
    else
      conversation_scope[:created_at] = range
    end
    ActsAsTaggableOn::Tagging
      .joins('INNER JOIN conversations ON taggings.taggable_id = conversations.id')
      .joins('INNER JOIN tags ON taggings.tag_id = tags.id')
      .where(taggable_type: 'Conversation', context: 'labels')
      .where(tags: { name: label })
      .where(conversations: conversation_scope)
      .count
  end
end
