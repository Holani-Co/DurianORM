class Reports::GoogleReviewsMailer < ApplicationMailer
  def monthly_report(account, recipients, since, till)
    return unless smtp_config_set_or_development?

    @account_name = account.name
    @period = since.strftime('%B %Y')
    csv = Reports::GoogleReviewsCsv.new(account: account, since: since, till: till).generate
    attachments["google-reviews-report-#{since}_to_#{till}.csv"] = {
      mime_type: 'text/csv',
      content: csv
    }

    send_mail_with_liquid(
      to: recipients,
      subject: "Google Reviews monthly report - #{@period}"
    )
  end

  private

  def liquid_locals
    super.merge(account_name: @account_name, period: @period)
  end
end
