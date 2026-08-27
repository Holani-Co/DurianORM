class Api::V1::Accounts::CampaignDeliveriesController < Api::V1::Accounts::BaseController
  before_action :check_authorization

  def index
    campaign = Current.account.campaigns.find_by!(display_id: params[:campaign_id])
    @campaign_deliveries = campaign.campaign_deliveries.includes(:contact).latest_first
    @campaign_deliveries = @campaign_deliveries.where(status: params[:status]) if params[:status].present?
  end
end
