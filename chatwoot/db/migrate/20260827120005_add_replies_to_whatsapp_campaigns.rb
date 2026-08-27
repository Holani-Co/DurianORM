class AddRepliesToWhatsappCampaigns < ActiveRecord::Migration[7.1]
  def change
    add_column :campaign_deliveries, :replied_at, :datetime
    add_index :campaign_deliveries, :replied_at
    add_column :campaigns, :reply_count, :integer, default: 0, null: false
  end
end
