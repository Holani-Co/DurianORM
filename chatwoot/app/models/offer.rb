# Durian — a promotional offer the client manages from the ORM Offers tab. The
# bot surfaces one on a customer's greeting (top priority for new customers; the
# best tag/AI match for known ones) and agents can send others manually.
#
# priority: lower number = shown first (#1 is the current headline).
# tags: product/category keywords used to match an offer to a customer's
#       interest (from the conversation context blob).
# == Schema Information
#
# Table name: offers
#
#  id         :bigint           not null, primary key
#  active     :boolean          default(TRUE), not null
#  caption    :string           not null
#  expires_at :datetime
#  link       :string
#  priority   :integer          default(0), not null
#  tags       :jsonb            not null
#  created_at :datetime         not null
#  updated_at :datetime         not null
#  account_id :bigint           not null
#
# Indexes
#
#  index_offers_on_account_id  (account_id)
#
class Offer < ApplicationRecord
  belongs_to :account
  has_one_attached :image

  validates :caption, presence: true

  # Active + not past its optional expiry, most important first.
  scope :live, lambda {
    where(active: true)
      .where('expires_at IS NULL OR expires_at > ?', Time.current)
      .order(priority: :asc, created_at: :desc)
  }

  def normalized_tags
    Array(tags).map { |t| t.to_s.strip.downcase }.reject(&:blank?)
  end
end
