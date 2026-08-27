# == Schema Information
#
# Table name: campaign_deliveries
#
#  id                  :bigint           not null, primary key
#  attempt_count       :integer          default(0), not null
#  delivered_at        :datetime
#  error_code          :string
#  error_message       :text
#  failed_at           :datetime
#  next_retry_at       :datetime
#  phone_number        :string
#  queued_at           :datetime
#  read_at             :datetime
#  recipient_snapshot  :jsonb            not null
#  sent_at             :datetime
#  skip_reason         :string
#  status              :string           default("pending"), not null
#  template_parameters :jsonb            not null
#  created_at          :datetime         not null
#  updated_at          :datetime         not null
#  account_id          :bigint           not null
#  campaign_id         :bigint           not null
#  contact_id          :bigint           not null
#  message_id          :bigint
#  meta_message_id     :string
#  whatsapp_consent_id :bigint
#
# Indexes
#
#  idx_campaign_deliveries_message                   (message_id) UNIQUE WHERE (message_id IS NOT NULL)
#  idx_campaign_deliveries_meta_message              (meta_message_id) UNIQUE WHERE (meta_message_id IS NOT NULL)
#  idx_campaign_deliveries_recipient                 (campaign_id,contact_id) UNIQUE
#  idx_campaign_deliveries_status                    (campaign_id,status)
#  index_campaign_deliveries_on_account_id           (account_id)
#  index_campaign_deliveries_on_campaign_id          (campaign_id)
#  index_campaign_deliveries_on_contact_id           (contact_id)
#  index_campaign_deliveries_on_next_retry_at        (next_retry_at)
#  index_campaign_deliveries_on_whatsapp_consent_id  (whatsapp_consent_id)
#
class CampaignDelivery < ApplicationRecord
  enum :status, {
    pending: 'pending',
    queued: 'queued',
    sending: 'sending',
    sent: 'sent',
    delivered: 'delivered',
    read: 'read',
    failed: 'failed',
    skipped: 'skipped',
    cancelled: 'cancelled'
  }, prefix: true

  belongs_to :account
  belongs_to :campaign
  belongs_to :contact
  belongs_to :message, optional: true
  belongs_to :whatsapp_consent, optional: true

  validates :phone_number, presence: true, unless: :status_skipped?
  validates :phone_number, format: { with: /\A\+[1-9]\d{1,14}\z/ }, unless: :status_skipped?
  validates :contact_id, uniqueness: { scope: :campaign_id }
  validates :meta_message_id, uniqueness: true, allow_nil: true
  validates :message_id, uniqueness: true, allow_nil: true
  validates :attempt_count, numericality: { only_integer: true, greater_than_or_equal_to: 0 }
  validate :campaign_belongs_to_account
  validate :contact_belongs_to_account
  validate :message_belongs_to_account
  validate :consent_belongs_to_account
  validate :snapshots_are_objects

  before_validation :normalize_identifiers

  scope :latest_first, -> { order(created_at: :desc) }
  scope :terminal, -> { where(status: %w[sent delivered read failed skipped cancelled]) }
  scope :dispatchable, -> { where(status: %w[pending queued]) }

  private

  def normalize_identifiers
    self.phone_number = phone_number.to_s.strip.presence
    self.meta_message_id = meta_message_id.to_s.strip.presence
  end

  def campaign_belongs_to_account
    errors.add(:campaign_id, 'must belong to the same account as the delivery') if campaign.present? && campaign.account_id != account_id
  end

  def contact_belongs_to_account
    errors.add(:contact_id, 'must belong to the same account as the delivery') if contact.present? && contact.account_id != account_id
  end

  def message_belongs_to_account
    errors.add(:message_id, 'must belong to the same account as the delivery') if message.present? && message.account_id != account_id
  end

  def consent_belongs_to_account
    return if whatsapp_consent.blank? || whatsapp_consent.account_id == account_id

    errors.add(:whatsapp_consent_id, 'must belong to the same account as the delivery')
  end

  def snapshots_are_objects
    errors.add(:recipient_snapshot, 'must be an object') unless recipient_snapshot.is_a?(Hash)
    errors.add(:template_parameters, 'must be an object') unless template_parameters.is_a?(Hash)
  end
end
