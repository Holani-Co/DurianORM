class CreateGoogleReviewStoreStats < ActiveRecord::Migration[7.1]
  def change
    create_table :google_review_store_stats do |t|
      t.references :account, null: false, foreign_key: true, index: true
      t.string :store_label, null: false
      t.string :title
      t.decimal :average_rating, precision: 2, scale: 1
      t.integer :total_review_count, default: 0, null: false
      t.timestamps
    end

    add_index :google_review_store_stats, [:account_id, :store_label], unique: true
  end
end
