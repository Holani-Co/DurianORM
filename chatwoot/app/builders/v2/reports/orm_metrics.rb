# Durian — shared helpers for the ORM report builders (Overview, AI
# Performance, CRM funnel, Reviews). Keeps range parsing and label counting in
# one place so the tiles and their drill-throughs stay consistent.
module V2::Reports::OrmMetrics
  # messages.content_attributes is a json column behind a JSON-coded `store`, so
  # rows hold a JSON *string* ("{\"source\":…}") rather than an object and a
  # plain `content_attributes ->> 'key'` never matches. This unwraps it (works
  # for either form).
  MESSAGE_ATTRS = "(CASE WHEN json_typeof(messages.content_attributes) = 'string' " \
                  "THEN (messages.content_attributes #>> '{}')::json ELSE messages.content_attributes END)".freeze

  # SQL for one key of a message's content_attributes, e.g. message_attr('source').
  def message_attr(key)
    "#{MESSAGE_ATTRS} ->> '#{key}'"
  end

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
  # in the range (or on_range, e.g. the previous period) — or, with only_open,
  # the live count still open (the "now" queue). Matches the label view the
  # matching tile drills into.
  def label_count(label, only_open: false, on_range: nil)
    conversation_scope = { account_id: account.id }
    if only_open
      conversation_scope[:status] = Conversation.statuses[:open]
    else
      conversation_scope[:created_at] = on_range || range
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
