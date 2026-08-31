class AddCampaignExecutionIndexes < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def up
    add_index :campaigns, :whatsapp_template_id, algorithm: :concurrently, if_not_exists: true
    add_index :campaigns, [:account_id, :execution_status],
              name: 'idx_campaigns_account_execution_status', algorithm: :concurrently, if_not_exists: true
  end

  def down
    remove_index :campaigns, name: 'idx_campaigns_account_execution_status', algorithm: :concurrently, if_exists: true
    remove_index :campaigns, :whatsapp_template_id, algorithm: :concurrently, if_exists: true
  end
end
