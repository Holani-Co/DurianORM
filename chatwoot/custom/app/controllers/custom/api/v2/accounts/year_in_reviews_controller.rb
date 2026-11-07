# The year in review over a Durian OS AI connection (docs/mcp/MCP.md, section 7): the browser's GET stores the built
# review in the person's ui_settings; the AI path returns the stored copy, or builds it and returns it without
# storing (every AI request reads only).
module Custom::Api::V2::Accounts::YearInReviewsController
  def show
    return super unless request.env.key?(DurianOs::Ai::ENV_KEY)

    year = params[:year] || 2025
    stored = Current.user.ui_settings&.dig("year_in_review_#{Current.account.id}_#{year}")
    return render(json: stored) if stored.present?

    render json: YearInReviewBuilder.new(account: Current.account, user_id: Current.user.id, year: year).build
  end
end
