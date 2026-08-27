class AddExecutionLifecycleToCampaigns < ActiveRecord::Migration[7.1]
  def up
    add_reference :campaigns, :whatsapp_template, null: true, index: true
    add_column :campaigns, :execution_status, :integer
    add_column :campaigns, :audience_snapshot_at, :datetime
    add_column :campaigns, :execution_started_at, :datetime
    add_column :campaigns, :execution_completed_at, :datetime
    add_column :campaigns, :execution_error, :text
    add_index :campaigns, [:account_id, :execution_status], name: 'idx_campaigns_account_execution_status'

    # Existing one-off campaigns retain their legacy meaning. Ongoing web
    # campaigns deliberately keep execution_status=NULL because this lifecycle
    # belongs only to finite, recipient-based campaigns.
    execute <<~SQL.squish
      UPDATE campaigns
      SET execution_status = CASE campaign_status
        WHEN 1 THEN 5
        ELSE 1
      END
      WHERE campaign_type = 1
    SQL
  end

  def down
    remove_index :campaigns, name: 'idx_campaigns_account_execution_status'
    remove_column :campaigns, :execution_error
    remove_column :campaigns, :execution_completed_at
    remove_column :campaigns, :execution_started_at
    remove_column :campaigns, :audience_snapshot_at
    remove_column :campaigns, :execution_status
    remove_reference :campaigns, :whatsapp_template
  end
end
