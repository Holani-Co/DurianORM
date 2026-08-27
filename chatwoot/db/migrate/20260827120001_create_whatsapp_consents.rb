class CreateWhatsappConsents < ActiveRecord::Migration[7.1]
  def change
    create_table :whatsapp_consents do |t|
      t.references :account, null: false, index: true
      t.references :inbox, null: false, index: true
      t.references :contact, null: false, index: true
      t.string :purpose, null: false, default: 'MARKETING'
      t.string :status, null: false
      t.string :source, null: false
      t.string :source_reference
      t.datetime :recorded_at, null: false
      t.jsonb :details, null: false, default: {}
      t.timestamps
    end

    add_index :whatsapp_consents, [:inbox_id, :contact_id, :purpose, :recorded_at],
              name: 'idx_wa_consents_current_lookup'
    add_index :whatsapp_consents, [:account_id, :inbox_id, :source, :source_reference],
              unique: true,
              where: 'source_reference IS NOT NULL',
              name: 'idx_wa_consents_source_reference'
  end
end
