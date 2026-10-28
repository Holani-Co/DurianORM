# Emails the deal owners — routing config owners and store owners — whose Zoho
# CRM user has been deactivated or deleted (~9:15 AM IST via config/schedule.yml),
# with the replacement the bridge suggests. Deals no longer fail on such owners
# (the bridge swaps them at deal time), but a person still has to pick the
# permanent owner. Recipients come from OWNER_SYNC_ALERT_RECIPIENTS; with none
# set, or nothing to report, the job sends nothing.
class Reports::SendOwnerSyncAlertJob < ApplicationJob
  queue_as :scheduled_jobs

  def perform
    return if recipients.empty?

    report = fetch_report
    return if report.blank? || Array(report['problems']).empty?

    Reports::OwnerSyncMailer.alert(recipients, report).deliver_now
  end

  private

  def recipients
    ENV.fetch('OWNER_SYNC_ALERT_RECIPIENTS', '').split(',').map(&:strip).compact_blank.uniq
  end

  def fetch_report
    response = HTTParty.get(
      "#{ENV.fetch('ZOHO_BRIDGE_URL', 'http://127.0.0.1:8420')}/admin/owner-sync",
      headers: { 'X-Routing-Admin-Secret' => ENV.fetch('ROUTING_ADMIN_SECRET', '') },
      timeout: 20
    )
    response.success? ? response.parsed_response : nil
  rescue StandardError => e
    Rails.logger.error("[owner-sync alert] bridge unreachable: #{e.message}")
    nil
  end
end
