# Durian — Forwarded Emails weekly report for one email category, sent on demand
# from Reports → Forwarded Emails to whoever the admin picks.
class Reports::OrmForwardedMailer < ApplicationMailer
  # week: 'YYYY-MM-DD', any day of the Mon–Sun week (nil → last week, India time).
  def weekly_report(account, recipients, category:, week: nil)
    return unless smtp_config_set_or_development?
    return if recipients.blank?

    data = V2::Reports::OrmForwardedReportBuilder.new(account: account, category: category, week: week).build
    @category_name = data[:category_name]
    @week_label = data[:week_label]
    @summary_html = Reports::OrmForwardedEmailSummary.new(data).html
    attachments["durian-forwarded-#{category.dasherize}-#{data[:week_key]}.xlsx"] = {
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      content: Reports::OrmForwardedXlsx.new(data).render
    }
    send_mail_with_liquid(to: recipients, subject: "Durian ORM - #{@category_name} emails forwarded - #{@week_label}")
  end

  private

  def liquid_locals
    super.merge(category_name: @category_name, week_label: @week_label, summary_html: @summary_html)
  end
end
