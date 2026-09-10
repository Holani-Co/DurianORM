class GoogleReviewStoreStat < ApplicationRecord
  belongs_to :account
  validates :store_label, presence: true, uniqueness: { scope: :account_id }
end
