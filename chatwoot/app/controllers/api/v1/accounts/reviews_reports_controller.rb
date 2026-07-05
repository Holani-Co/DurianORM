# Durian — downloadable Google-reviews ratings report.
#
# GET /api/v1/accounts/:account_id/reviews_report?inbox_id=&since=YYYY-MM-DD&until=YYYY-MM-DD
#
# Returns a CSV for the requested period: a star-segregated summary
# (5★..1★ counts, total, average) followed by one row per review. Reviews
# are bucketed by their ACTUAL Google posting date
# (additional_attributes.review_created_at, backfilled by the bridge) — not
# ingestion time. The handful of legacy reviews with no posting date are
# excluded from dated reports.
#
# Defaults to the last 7 days when no range is given — "this week's report"
# is the client's primary use case (download → forward to stakeholders).
class Api::V1::Accounts::ReviewsReportsController < Api::V1::Accounts::BaseController
  STATUS_LABELS = {
    'review-auto-replied' => 'Auto-replied',
    'review-manually-replied' => 'Manually replied',
    'review-replied' => 'Replied'
  }.freeze

  def show
    inbox = Current.account.inboxes.find(params[:inbox_id])
    since = parse_date(params[:since]) || 6.days.ago.to_date
    till = parse_date(params[:until]) || Date.current

    rows = review_rows(inbox, since, till)
    send_data build_csv(rows, since, till),
              filename: "google-reviews-report-#{since}_to_#{till}.csv",
              type: 'text/csv'
  end

  private

  def parse_date(value)
    Date.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end

  # A few hundred review conversations per account — filter/parse in Ruby
  # rather than casting the ISO string in SQL (a single malformed
  # review_created_at would abort the whole query on a ::timestamptz cast).
  def review_rows(inbox, since, till)
    convs = inbox.conversations.where("additional_attributes ->> 'type' = 'google_review'")
    rows = convs.filter_map do |conv|
      attrs = conv.additional_attributes || {}
      posted_at = safe_time(attrs['review_created_at'])
      next if posted_at.nil? || posted_at.to_date < since || posted_at.to_date > till

      review_row(conv, attrs, posted_at)
    end
    rows.sort_by { |r| r[:date] }.reverse
  end

  def review_row(conv, attrs, posted_at)
    labels = conv.cached_label_list_array
    {
      date: posted_at.to_date,
      stars: attrs['stars'].to_i,
      location: attrs['location'].to_s,
      reviewer: attrs['reviewer'].to_s,
      comment: attrs['review_comment'].to_s,
      status: reply_status(labels),
      agent: replied_by(labels)
    }
  end

  def safe_time(value)
    Time.zone.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end

  def reply_status(labels)
    STATUS_LABELS.each { |label, status| return status if labels.include?(label) }
    'Unreplied'
  end

  def replied_by(labels)
    slug = labels.find { |l| l.start_with?('replied-by-') }
    slug&.delete_prefix('replied-by-')&.tr('-', ' ')&.titleize.to_s
  end

  def build_csv(rows, since, till)
    CSV.generate do |csv|
      csv << ['Durian - Google Reviews Report']
      csv << ['Period', "#{since} to #{till}"]
      csv << []
      summary_rows(rows).each { |row| csv << row }
      csv << []
      csv << ['Date', 'Stars', 'Store', 'Reviewer', 'Review', 'Reply status', 'Replied by']
      rows.each do |r|
        csv << [r[:date], r[:stars].zero? ? '' : r[:stars], r[:location], r[:reviewer],
                r[:comment], r[:status], r[:agent]]
      end
    end
  end

  def summary_rows(rows)
    rated = rows.reject { |r| r[:stars].zero? }
    average = rated.any? ? (rated.sum { |r| r[:stars] }.to_f / rated.size).round(2) : '-'
    [%w[Rating Count]] +
      5.downto(1).map { |s| ["#{s} star", rows.count { |r| r[:stars] == s }] } +
      [['Unrated', rows.count { |r| r[:stars].zero? }],
       ['Total reviews', rows.size],
       ['Average rating', average]]
  end
end
