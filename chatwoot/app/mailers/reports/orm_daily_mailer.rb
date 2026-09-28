class Reports::OrmDailyMailer < ApplicationMailer
  # date: nil → yesterday (India time). Pass 'YYYY-MM-DD' to rebuild a past day.
  def daily_report(account, recipients, date: nil)
    return unless smtp_config_set_or_development?
    return if recipients.blank?

    data = V2::Reports::OrmDailyReportBuilder.new(account: account, date: date).build
    @account_name = account.name
    @date_label = data[:date_label]
    @summary_html = Reports::OrmDailyEmailSummary.new(data).html
    attachments["durian-orm-daily-#{data[:date_key]}.xlsx"] = {
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      content: Reports::OrmDailyXlsx.new(data).render
    }
    send_mail_with_liquid(to: recipients, subject: "Durian ORM daily report - #{@date_label}")
  end

  private

  def liquid_locals
    super.merge(account_name: @account_name, date_label: @date_label, summary_html: @summary_html)
  end
end
