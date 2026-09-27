# Durian — WhatsApp campaign section of the ORM Monthly Report: messages sent
# (or failed before sending) in a period, per campaign, plus why they failed.
class V2::Reports::OrmMonthlyCampaigns
  COLUMNS = %i[campaign_id attempted sent delivered read failed].freeze
  FAILURE_REASONS = {
    '131049' => 'Meta limit — marketing message held back (healthy ecosystem cap)',
    '131042' => 'WhatsApp billing / payment issue',
    '131026' => 'Number not reachable on WhatsApp',
    '131047' => 'Outside the 24-hour reply window',
    '131048' => 'Spam rate limit',
    '131050' => 'Customer stopped marketing messages',
    '130429' => 'Meta rate limit'
  }.freeze

  def initialize(account:, range:)
    @account = account
    @range = range
  end

  def summary
    rows = campaign_rows
    { totals: %i[attempted sent delivered read failed].index_with { |k| rows.sum { |r| r[k] } },
      by_campaign: rows, failure_reasons: failure_rows }
  end

  def delivered_count
    deliveries.where('delivered_at IS NOT NULL OR read_at IS NOT NULL').count
  end

  private

  def deliveries
    CampaignDelivery.where(account_id: @account.id)
                    .where('COALESCE(campaign_deliveries.sent_at, campaign_deliveries.failed_at) >= ? ' \
                           'AND COALESCE(campaign_deliveries.sent_at, campaign_deliveries.failed_at) < ?',
                           @range.begin, @range.end)
  end

  def campaign_rows
    counts = deliveries.group(:campaign_id).pluck(
      :campaign_id, Arel.sql('COUNT(*)'), Arel.sql('COUNT(sent_at)'),
      Arel.sql('COUNT(*) FILTER (WHERE delivered_at IS NOT NULL OR read_at IS NOT NULL)'),
      Arel.sql('COUNT(read_at)'), Arel.sql("COUNT(*) FILTER (WHERE status = 'failed')")
    ).map { |values| COLUMNS.zip(values).to_h }
    titles = Campaign.where(id: counts.pluck(:campaign_id)).pluck(:id, :title).to_h
    counts.map { |row| row.merge(name: titles[row[:campaign_id]] || "Campaign #{row[:campaign_id]}") }
          .sort_by { |row| -row[:attempted] }
  end

  def failure_rows
    deliveries.where(status: 'failed').group(:error_code).count
              .map { |code, n| { code: code, reason: failure_reason(code), count: n } }
              .sort_by { |row| -row[:count] }
  end

  def failure_reason(code)
    FAILURE_REASONS[code.to_s] || (code.present? ? "Meta error #{code}" : 'Unknown')
  end
end
