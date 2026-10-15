# Durian — Forwarded Emails reports.
#   • weekly_report: one email category's Mon–Sun report, sent on demand from
#     Reports → Forwarded Emails AND automatically every Monday to the category's
#     team (SendWeeklyForwardedReportsJob).
#   • monthly_overall_report: ALL categories for a calendar month in one bigger
#     report, sent automatically on the 1st to Shilpi (SendMonthlyForwardedReportJob).
# Both greet the recipient by name (FORWARDED_REPORT_NAME_MAP, else derived from
# the email), so it reads "Hello <name>, here is your report".
class Reports::OrmForwardedMailer < ApplicationMailer
  # week: 'YYYY-MM-DD', any day of the Mon–Sun week (nil → last week, India time).
  def weekly_report(account, recipients, category:, category_name:, week: nil)
    return unless smtp_config_set_or_development?

    data = V2::Reports::OrmForwardedReportBuilder.new(account: account, category: category, week: week).build
    @category_name = category_name
    @week_label = data[:week_label]
    @greeting = greeting_line(recipients)
    @summary_html = Reports::OrmForwardedEmailSummary.new(data).html
    attachments["durian-forwarded-#{category.dasherize}-#{data[:week_key]}.xlsx"] = {
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      content: Reports::OrmForwardedXlsx.new(data).render
    }
    send_mail_with_liquid(to: recipients, subject: "Durian ORM - #{@category_name} emails forwarded - #{@week_label}")
  end

  # month: 'YYYY-MM-DD', any day of the month (nil → last month, India time).
  # Every forwarding category for that month in one report (Category column), for
  # an overall-tracking recipient such as Shilpi.
  def monthly_overall_report(account, recipients, month: nil)
    return unless smtp_config_set_or_development?

    data = V2::Reports::OrmForwardedReportBuilder.new(account: account, category: nil, month: month).build
    @month_label = data[:period_label]
    @greeting = greeting_line(recipients)
    @summary_html = Reports::OrmForwardedEmailSummary.new(data).html
    attachments["durian-forwarded-all-categories-#{data[:week_key]}.xlsx"] = {
      mime_type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
      content: Reports::OrmForwardedMonthlyXlsx.new(data).render
    }
    send_mail_with_liquid(to: recipients, subject: "Durian ORM - all forwarded emails - #{@month_label}")
  end

  private

  def liquid_locals
    super.merge(category_name: @category_name, week_label: @week_label,
                month_label: @month_label, greeting: @greeting, summary_html: @summary_html)
  end

  # "Hello <name>," for the first recipient — the person the report is for.
  def greeting_line(recipients)
    name = greeting_name(Array(recipients).first.to_s)
    name.present? ? "Hello #{name}," : 'Hello,'
  end

  # A configured email→name map (FORWARDED_REPORT_NAME_MAP as JSON) wins; else the
  # email's local part, first token, title-cased (shilpa@durian.in → "Shilpa").
  def greeting_name(email)
    email = email.to_s.strip.downcase
    return '' if email.blank?

    mapped = name_map[email]
    return mapped if mapped.present?

    local = email.split('@').first.to_s.split(/[._-]/).first.to_s
    local.present? ? local.capitalize : ''
  end

  def name_map
    @name_map ||= begin
      JSON.parse(ENV.fetch('FORWARDED_REPORT_NAME_MAP', '{}')).transform_keys { |k| k.to_s.strip.downcase }
    rescue JSON::ParserError
      {}
    end
  end
end
