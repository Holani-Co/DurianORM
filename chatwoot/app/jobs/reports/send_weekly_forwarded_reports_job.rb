# Emails each forwarding email-category's previous Mon–Sun report to that
# category's own team, every Monday ~9 AM IST (config/schedule.yml). Recipients
# are the category's live forward_to from the bridge routing config — the same
# people who receive that category's forwards. Dark-launched: off unless
# WEEKLY_FORWARDED_REPORTS_ENABLED=true, so it never emails until switched on.
class Reports::SendWeeklyForwardedReportsJob < ApplicationJob
  queue_as :scheduled_jobs

  ORM_ACCOUNT_ID = ENV.fetch('ORM_ACCOUNT_ID', '1').to_i

  # week: any day 'YYYY-MM-DD' of the week to report; nil → last week (India time).
  def perform(week: nil)
    return unless enabled?

    account = Account.find_by(id: ORM_ACCOUNT_ID)
    return unless account

    categories = Reports::ForwardingCategories.list
    return if categories.blank?

    categories.each do |category|
      recipients = split_recipients(category[:forward_to])
      next if recipients.empty?

      Reports::OrmForwardedMailer.with(account: account)
                                 .weekly_report(account, recipients,
                                                category: category[:key], category_name: category[:name], week: week)
                                 .deliver_later
    end
  end

  private

  def enabled?
    ENV.fetch('WEEKLY_FORWARDED_REPORTS_ENABLED', 'false').to_s.downcase == 'true'
  end

  def split_recipients(value)
    value.to_s.split(/[\s,;]+/).compact_blank.uniq
  end
end
