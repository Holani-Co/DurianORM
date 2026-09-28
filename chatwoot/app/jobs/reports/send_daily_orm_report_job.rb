# Emails the previous day's ORM report (~9 AM IST via config/schedule.yml) to
# management. Recipients come from DAILY_ORM_REPORT_RECIPIENTS (comma-separated);
# with none set the job no-ops, so it never emails an unintended address.
class Reports::SendDailyOrmReportJob < ApplicationJob
  queue_as :scheduled_jobs

  ORM_ACCOUNT_ID = ENV.fetch('ORM_ACCOUNT_ID', '1').to_i

  def perform(date: nil)
    return if recipients.empty?

    account = Account.find_by(id: ORM_ACCOUNT_ID)
    return unless account

    Reports::OrmDailyMailer.with(account: account)
                           .daily_report(account, recipients, date: date)
                           .deliver_now
  end

  private

  def recipients
    ENV.fetch('DAILY_ORM_REPORT_RECIPIENTS', '').split(',').map(&:strip).compact_blank.uniq
  end
end
