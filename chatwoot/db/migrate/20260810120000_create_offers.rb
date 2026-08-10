# Durian — offers the client manages from the ORM and the bot surfaces on a
# greeting. Image lives in ActiveStorage (has_one_attached :image on the model).
class CreateOffers < ActiveRecord::Migration[7.1]
  def change
    create_table :offers do |t|
      t.references :account, null: false, index: true
      t.string :caption, null: false
      t.integer :priority, null: false, default: 0
      t.boolean :active, null: false, default: true
      t.jsonb :tags, null: false, default: []
      t.datetime :expires_at
      t.timestamps
    end
  end
end
