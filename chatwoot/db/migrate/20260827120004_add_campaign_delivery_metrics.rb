class AddCampaignDeliveryMetrics < ActiveRecord::Migration[7.1]
  def change
    change_column_null :campaign_deliveries, :phone_number, true
    add_column :campaign_deliveries, :skip_reason, :string
    add_column :campaign_deliveries, :next_retry_at, :datetime
    add_index :campaign_deliveries, :next_retry_at

    add_column :campaigns, :audience_count, :integer, null: false, default: 0
    add_column :campaigns, :eligible_count, :integer, null: false, default: 0
    add_column :campaigns, :skipped_count, :integer, null: false, default: 0
    add_column :campaigns, :sent_count, :integer, null: false, default: 0
    add_column :campaigns, :delivered_count, :integer, null: false, default: 0
    add_column :campaigns, :read_count, :integer, null: false, default: 0
    add_column :campaigns, :failed_count, :integer, null: false, default: 0
  end
end
