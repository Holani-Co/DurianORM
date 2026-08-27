class CampaignDeliveryPolicy < ApplicationPolicy
  def index?
    @account_user.administrator?
  end

  def export?
    @account_user.administrator?
  end
end
