class AddWhatsappConsentOptinToDataImports < ActiveRecord::Migration[7.1]
  def change
    # Carry the WhatsApp-consent-opt-in import's chosen inbox + label into the job.
    # Nullable so existing contact imports are unaffected.
    add_column :data_imports, :inbox_id, :bigint, null: true
    add_column :data_imports, :label, :string, null: true
  end
end
