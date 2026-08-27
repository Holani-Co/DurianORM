# == Schema Information
#
# Table name: whatsapp_consents
#
#  id               :bigint           not null, primary key
#  details          :jsonb            not null
#  purpose          :string           default("MARKETING"), not null
#  recorded_at      :datetime         not null
#  source           :string           not null
#  source_reference :string
#  status           :string           not null
#  created_at       :datetime         not null
#  updated_at       :datetime         not null
#  account_id       :bigint           not null
#  contact_id       :bigint           not null
#  inbox_id         :bigint           not null
#
# Indexes
#
#  idx_wa_consents_current_lookup         (inbox_id,contact_id,purpose,recorded_at)
#  idx_wa_consents_source_reference       (account_id,inbox_id,source,source_reference) UNIQUE WHERE (source_reference IS NOT NULL)
#  index_whatsapp_consents_on_account_id  (account_id)
#  index_whatsapp_consents_on_contact_id  (contact_id)
#  index_whatsapp_consents_on_inbox_id    (inbox_id)
#
class WhatsappConsent < ApplicationRecord
  PURPOSES = %w[MARKETING].freeze
  STATUSES = %w[OPTED_IN OPTED_OUT].freeze

  belongs_to :account
  belongs_to :inbox
  belongs_to :contact

  has_many :campaign_deliveries, dependent: :nullify

  validates :purpose, inclusion: { in: PURPOSES }
  validates :status, inclusion: { in: STATUSES }
  validates :source, :recorded_at, presence: true
  validate :associations_belong_to_account
  validate :details_are_an_object
  validate :inbox_is_whatsapp

  before_validation :normalize_fields

  scope :latest_first, -> { order(recorded_at: :desc, id: :desc) }
  scope :opted_in, -> { where(status: 'OPTED_IN') }
  scope :opted_out, -> { where(status: 'OPTED_OUT') }

  def self.current_for(inbox:, contact:, purpose: 'MARKETING')
    where(inbox: inbox, contact: contact, purpose: purpose.to_s.upcase).latest_first.first
  end

  def opted_in?
    status == 'OPTED_IN'
  end

  private

  def normalize_fields
    self.purpose = purpose.to_s.strip.upcase.presence
    self.status = status.to_s.strip.upcase.presence
    self.source = source.to_s.strip.presence
    self.source_reference = source_reference.to_s.strip.presence
    self.recorded_at ||= Time.current
  end

  def associations_belong_to_account
    errors.add(:inbox_id, 'must belong to the same account as the consent') if inbox.present? && inbox.account_id != account_id
    errors.add(:contact_id, 'must belong to the same account as the consent') if contact.present? && contact.account_id != account_id
  end

  def details_are_an_object
    errors.add(:details, 'must be an object') unless details.is_a?(Hash)
  end

  def inbox_is_whatsapp
    errors.add(:inbox_id, 'must be a WhatsApp inbox') if inbox.present? && inbox.inbox_type != 'Whatsapp'
  end
end
