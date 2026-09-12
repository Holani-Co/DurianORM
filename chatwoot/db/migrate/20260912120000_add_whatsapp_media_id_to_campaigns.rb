class AddWhatsappMediaIdToCampaigns < ActiveRecord::Migration[7.1]
  def change
    add_column :campaigns, :whatsapp_media_id, :string
  end
end
