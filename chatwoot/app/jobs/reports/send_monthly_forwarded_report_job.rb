# Emails the previous calendar month's ALL-categories forwarded-emails report to
# an overall-tracking recipient (Shilpi) on the 1st ~10 AM IST (config/schedule.yml).
# Recipients come from MONTHLY_FORWARDED_REPORT_RECIPIENTS (default shilpi@durian.in).
# Dark-launched: off unless MONTHLY_FORWARDED_REPORT_ENABLED=true.
class Reports::SendMonthlyForwardedReportJob < ApplicationJob
  queue_as :scheduled_jobs

  ORM_ACCOUNT_ID = ENV.fetch('ORM_ACCOUNT_ID', '1').to_i

  # month: any day 'YYYY-MM-DD' of the month to report; nil → last month (India time).
  def perform(month: nil)
    return unless enabled?
    return if recipients.empty?

    account = Account.find_by(id: ORM_ACCOUNT_ID)
    return unless account

    Reports::OrmForwardedMailer.with(account: account)
                               .monthly_overall_report(account, recipients, month: month)
                               .deliver_later
  end

  private

  def enabled?
    ENV.fetch('MONTHLY_FORWARDED_REPORT_ENABLED', 'false').to_s.downcase == 'true'
  end

  def recipients
    ENV.fetch('MONTHLY_FORWARDED_REPORT_RECIPIENTS', 'shilpi@durian.in').split(/[\s,;]+/).compact_blank.uniq
  end
end
