class CreateWhatsappTemplates < ActiveRecord::Migration[7.1]
  def change
    create_table :whatsapp_templates do |t|
      add_ownership_columns(t)
      add_template_columns(t)
      add_review_columns(t)
      t.timestamps
    end

    add_index :whatsapp_templates, [:inbox_id, :name, :language], unique: true,
                                                                  name: 'idx_wa_templates_inbox_name_language'
    add_index :whatsapp_templates, [:inbox_id, :meta_template_id], unique: true,
                                                                   where: 'meta_template_id IS NOT NULL',
                                                                   name: 'idx_wa_templates_inbox_meta_id'
    add_index :whatsapp_templates, [:account_id, :status], name: 'idx_wa_templates_account_status'
  end

  private

  def add_ownership_columns(table)
    table.references :account, null: false, index: true
    table.references :inbox, null: false, index: true
    table.references :submitted_by, null: true, index: true
  end

  def add_template_columns(table)
    table.string :meta_template_id
    table.string :name, null: false
    table.string :language, null: false
    table.string :category, null: false
    table.jsonb :components, null: false, default: []
  end

  def add_review_columns(table)
    table.string :status, null: false, default: 'DRAFT'
    table.text :rejection_reason
    table.string :quality_rating
    table.datetime :submitted_at
    table.datetime :approved_at
    table.datetime :rejected_at
    table.datetime :last_synced_at
  end
end
