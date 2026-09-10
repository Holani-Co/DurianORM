class Reports::SendMonthlyGoogleReviewsReportJob < ApplicationJob
  queue_as :scheduled_jobs

  DEFAULT_RECIPIENTS = %w[
    nagendra.rao@durian.in
    sujitha.acharya@durian.in
    varsha@durian.in
    shilpa@durian.in
  ].freeze
  GOOGLE_REVIEWS_INBOX_NAME = 'Google Reviews'.freeze

  def perform
    report_till = Date.current.prev_month.end_of_month
    report_since = report_till.beginning_of_month

    Account.joins(:inboxes).where(inboxes: { name: GOOGLE_REVIEWS_INBOX_NAME }).distinct.find_each do |account|
      Reports::GoogleReviewsMailer
        .with(account: account)
        .monthly_report(account, recipients, report_since, report_till)
        .deliver_now
    end
  end

  private

  def recipients
    configured = ENV.fetch('GOOGLE_REVIEWS_MONTHLY_REPORT_RECIPIENTS', DEFAULT_RECIPIENTS.join(','))
    configured.split(',').map(&:strip).compact_blank.uniq
  end
end
