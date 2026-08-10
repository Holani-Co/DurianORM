# Durian — AI Performance report.
#
# How well the assistant is carrying the social inbox: how much it answered on
# its own, how confident it was, how often it handed off, which templates did
# the work, and which intent gates it resolved automatically. Reads the
# ai_auto_reply marker + confidence + short_code the bridge stamps on every
# auto-sent message, plus the intent labels.
class V2::Reports::AiPerformanceBuilder
  include V2::Reports::OrmMetrics

  # Intent gates, shown as "answered automatically" — each maps to the label the
  # bridge tags when that gate fires.
  GATE_LABELS = {
    'EMI' => 'emi-enquiry',
    'Store address' => 'retail-routed',
    'Order lookup' => 'order-details-needed',
    'Deal' => 'deal-created'
  }.freeze

  def initialize(account:, params:)
    @account = account
    @params = params
  end

  def build
    {
      range: { since: range.begin.to_i, until: range.end.to_i },
      summary: summary,
      template_usage: template_usage,
      confidence_buckets: confidence_buckets,
      gates: gates
    }
  end

  private

  attr_reader :account, :params

  def auto_replies
    # reorder(nil) drops the messages default created_at order, which would
    # otherwise force messages.created_at into GROUP BY (template_usage).
    @auto_replies ||= account.messages.reorder(nil)
                             .where(created_at: range)
                             .where("content_attributes ->> 'source' = 'ai_auto_reply'")
  end

  def summary
    sent = auto_replies.count
    handled = auto_replies.distinct.count(:conversation_id)
    total_convs = account.conversations.where(created_at: range).count
    {
      auto_replies_sent: sent,
      conversations_handled: handled,
      auto_send_rate: total_convs.positive? ? (handled * 100.0 / total_convs).round : 0,
      avg_confidence: avg_confidence,
      handoffs: label_count('agent-needed')
    }
  end

  def avg_confidence
    avg = auto_replies.where("content_attributes ->> 'confidence' ~ '^[0-9]+$'")
                      .average(Arel.sql("(content_attributes ->> 'confidence')::int"))
    avg ? avg.to_f.round : 0
  end

  # Which Durian templates the auto-sends used, most-used first.
  def template_usage
    auto_replies
      .where("content_attributes ->> 'short_code' IS NOT NULL")
      .group("content_attributes ->> 'short_code'")
      .count
      .sort_by { |_code, count| -count }
      .first(12)
      .to_h
  end

  # How sure the assistant was on the replies it sent, bucketed.
  def confidence_buckets
    buckets = { '90-100' => 0, '80-89' => 0, '60-79' => 0, 'Below 60' => 0 }
    auto_replies.where("content_attributes ->> 'confidence' ~ '^[0-9]+$'")
                .pluck(Arel.sql("(content_attributes ->> 'confidence')::int"))
                .each do |c|
      key = if c >= 90 then '90-100'
            elsif c >= 80 then '80-89'
            elsif c >= 60 then '60-79'
            else 'Below 60'
            end
      buckets[key] += 1
    end
    buckets
  end

  # Intent gates the assistant resolved in the period (by their labels).
  def gates
    GATE_LABELS.transform_values { |label| label_count(label) }
  end
end
