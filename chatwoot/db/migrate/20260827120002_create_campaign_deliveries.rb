class CreateCampaignDeliveries < ActiveRecord::Migration[7.1]
  def change
    create_table :campaign_deliveries do |t|
      add_association_columns(t)
      add_delivery_columns(t)
      add_result_columns(t)
      t.timestamps
    end

    add_index :campaign_deliveries, [:campaign_id, :contact_id], unique: true,
                                                                 name: 'idx_campaign_deliveries_recipient'
    add_index :campaign_deliveries, [:campaign_id, :status], name: 'idx_campaign_deliveries_status'
    add_index :campaign_deliveries, :message_id, unique: true,
                                                 where: 'message_id IS NOT NULL',
                                                 name: 'idx_campaign_deliveries_message'
    add_index :campaign_deliveries, :meta_message_id, unique: true,
                                                      where: 'meta_message_id IS NOT NULL',
                                                      name: 'idx_campaign_deliveries_meta_message'
  end

  private

  def add_association_columns(table)
    table.references :account, null: false, index: true
    table.references :campaign, null: false, index: true
    table.references :contact, null: false, index: true
    table.references :message, null: true, index: false
    table.references :whatsapp_consent, null: true, index: true
  end

  def add_delivery_columns(table)
    table.string :phone_number, null: false
    table.string :status, null: false, default: 'pending'
    table.string :meta_message_id
    table.jsonb :recipient_snapshot, null: false, default: {}
    table.jsonb :template_parameters, null: false, default: {}
    table.integer :attempt_count, null: false, default: 0
  end

  def add_result_columns(table)
    table.string :error_code
    table.text :error_message
    table.datetime :queued_at
    table.datetime :sent_at
    table.datetime :delivered_at
    table.datetime :read_at
    table.datetime :failed_at
  end
end
