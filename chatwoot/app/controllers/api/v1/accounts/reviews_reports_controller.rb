# Durian — downloadable Google-reviews ratings report.
#
# GET /api/v1/accounts/:account_id/reviews_report?since=&until=&inbox_id=
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
  def show
    since = parse_date(params[:since]) || 6.days.ago.to_date
    till = parse_date(params[:until]) || Date.current
    inbox = Current.account.inboxes.find(params[:inbox_id]) if params[:inbox_id].present?

    csv = Reports::GoogleReviewsCsv.new(account: Current.account, inbox: inbox, since: since, till: till).generate
    send_data csv,
              filename: "google-reviews-report-#{since}_to_#{till}.csv",
              type: 'text/csv'
  end

  private

  def parse_date(value)
    return Time.zone.at(value.to_i).to_date if value.to_s.match?(/\A\d+\z/)

    Date.parse(value.to_s)
  rescue ArgumentError, TypeError
    nil
  end
end
