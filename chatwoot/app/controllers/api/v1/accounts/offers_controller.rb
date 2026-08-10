# Durian — CRUD for the client-managed promotional offers (ORM Offers tab).
# Admin-only. The image is uploaded as a multipart file and stored in
# ActiveStorage (has_one_attached :image on Offer). The bridge reads the live
# offers to surface one on a customer's greeting.
class Api::V1::Accounts::OffersController < Api::V1::Accounts::BaseController
  # Reading is open to any agent (the bot fetches live offers for greetings);
  # managing offers is admin-only.
  before_action :check_admin, only: [:create, :update, :destroy]
  before_action :fetch_offer, only: [:update, :destroy]

  def index
    @offers = Current.account.offers.order(priority: :asc, created_at: :desc)
  end

  def create
    @offer = Current.account.offers.new(offer_params)
    @offer.save!
    @offer.image.attach(params[:image]) if params[:image].present?
  end

  def update
    @offer.update!(offer_params)
    @offer.image.attach(params[:image]) if params[:image].present?
  end

  def destroy
    @offer.destroy!
    head :ok
  end

  private

  def fetch_offer
    @offer = Current.account.offers.find(params[:id])
  end

  def offer_params
    params.permit(:caption, :priority, :active, :expires_at, tags: [])
  end

  def check_admin
    return if Current.account_user&.administrator?

    render json: { error: 'Admin access required' }, status: :unauthorized
  end
end
