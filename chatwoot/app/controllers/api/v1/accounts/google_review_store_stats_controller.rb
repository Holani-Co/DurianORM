class Api::V1::Accounts::GoogleReviewStoreStatsController < Api::V1::Accounts::BaseController
  def create
    stat = Current.account.google_review_store_stats.find_or_initialize_by(store_label: params[:store_label])
    stat.update!(title: params[:title], average_rating: params[:average_rating],
                 total_review_count: params[:total_review_count].to_i)
    render json: { id: stat.id }
  rescue ActiveRecord::RecordInvalid => e
    render json: { error: e.message }, status: :unprocessable_entity
  end
end
