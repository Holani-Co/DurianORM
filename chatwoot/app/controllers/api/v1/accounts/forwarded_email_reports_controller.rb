# Durian — Forwarded Emails weekly report (Reports → Forwarded Emails).
#
# For one email category, every customer email the ORM forwarded to a team in a
# Mon–Sun week (India time) and what came of it. The admin previews it here and
# sends it (summary + Excel attachment) to the address they enter, pre-filled
# with the category's forward address from the bridge's live routing rules.
class Api::V1::Accounts::ForwardedEmailReportsController < Api::V1::Accounts::BaseController
  before_action :check_authorization
  before_action :check_category, only: [:show, :deliver]

  # GET …/forwarded_email_report?category=collaboration_request&week=YYYY-MM-DD
  def show
    render json: V2::Reports::OrmForwardedReportBuilder.new(account: Current.account, category: params[:category], week: week).build
  end

  # GET …/forwarded_email_report/categories — the categories that email a team,
  # with the address each forwards to.
  def categories
    list = Reports::ForwardingCategories.list
    return render json: { error: 'bridge unavailable' }, status: :bad_gateway if list.nil?

    render json: list
  end

  # POST …/forwarded_email_report/deliver  { category, category_name, week, recipients: 'a@x.com, b@y.com' }
  def deliver
    if recipients.empty? || recipients.any? { |email| !email.match?(URI::MailTo::EMAIL_REGEXP) }
      return render json: { error: 'invalid recipients' }, status: :unprocessable_entity
    end

    Reports::OrmForwardedMailer.with(account: Current.account)
                               .weekly_report(Current.account, recipients,
                                              category: params[:category], category_name: category_name, week: week)
                               .deliver_later
    render json: { recipients: recipients }
  end

  private

  def recipients
    @recipients ||= params[:recipients].to_s.split(/[\s,;]+/).compact_blank.uniq
  end

  # The name the admin picked it by (the bridge's display name).
  def category_name
    params[:category_name].to_s.squish.presence || params[:category].humanize
  end

  # Any day of the week as YYYY-MM-DD; anything else means last week.
  def week
    Date.iso8601(params[:week].to_s).iso8601
  rescue Date::Error
    nil
  end

  def check_category
    return if params[:category].to_s.match?(/\A[a-z0-9_]+\z/)

    render json: { error: 'category is required' }, status: :unprocessable_entity
  end

  def check_authorization
    authorize :report, :view?
  end
end
