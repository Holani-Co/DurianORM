# == Schema Information
#
# Table name: whatsapp_templates
#
#  id               :bigint           not null, primary key
#  approved_at      :datetime
#  category         :string           not null
#  components       :jsonb            not null
#  language         :string           not null
#  last_synced_at   :datetime
#  name             :string           not null
#  quality_rating   :string
#  rejected_at      :datetime
#  rejection_reason :text
#  status           :string           default("DRAFT"), not null
#  submitted_at     :datetime
#  created_at       :datetime         not null
#  updated_at       :datetime         not null
#  account_id       :bigint           not null
#  inbox_id         :bigint           not null
#  meta_template_id :string
#  submitted_by_id  :bigint
#
# Indexes
#
#  idx_wa_templates_account_status              (account_id,status)
#  idx_wa_templates_inbox_meta_id               (inbox_id,meta_template_id) UNIQUE WHERE (meta_template_id IS NOT NULL)
#  idx_wa_templates_inbox_name_language         (inbox_id,name,language) UNIQUE
#  index_whatsapp_templates_on_account_id       (account_id)
#  index_whatsapp_templates_on_inbox_id         (inbox_id)
#  index_whatsapp_templates_on_submitted_by_id  (submitted_by_id)
#
class WhatsappTemplate < ApplicationRecord
  CATEGORIES = %w[MARKETING UTILITY AUTHENTICATION].freeze

  belongs_to :account
  belongs_to :inbox
  belongs_to :submitted_by, class_name: 'User', optional: true

  has_many :campaigns, dependent: :nullify

  validates :name, presence: true,
                   format: { with: /\A[a-z0-9_]+\z/ },
                   uniqueness: { scope: [:inbox_id, :language] }
  validates :language, :status, presence: true
  validates :category, inclusion: { in: CATEGORIES }
  validates :meta_template_id, uniqueness: { scope: :inbox_id }, allow_nil: true
  validate :inbox_belongs_to_account
  validate :submitted_by_belongs_to_account
  validate :components_are_an_array
  validate :components_include_body

  before_validation :normalize_meta_fields

  scope :approved, -> { where(status: 'APPROVED') }
  scope :latest_first, -> { order(updated_at: :desc) }

  def approved?
    status == 'APPROVED'
  end

  def processor_payload
    {
      'name' => name,
      'language' => language,
      'category' => category,
      'status' => status,
      'components' => components
    }
  end

  private

  def normalize_meta_fields
    self.name = normalize(name)&.downcase
    self.language = normalize(language)
    self.category = normalize(category)&.upcase
    self.status = normalize(status)&.upcase
    self.meta_template_id = normalize(meta_template_id)
  end

  def normalize(value)
    value.to_s.strip.presence
  end

  def inbox_belongs_to_account
    return if inbox.blank? || account.blank? || inbox.account_id == account_id

    errors.add(:inbox_id, 'must belong to the same account as the template')
  end

  def components_are_an_array
    errors.add(:components, 'must be an array') unless components.is_a?(Array)
  end

  def components_include_body
    return unless components.is_a?(Array)

    body = components.find { |component| component['type'].to_s.upcase == 'BODY' }
    return if body.present? && (body['text'].present? || category == 'AUTHENTICATION')

    errors.add(:components, 'must include a body with text')
  end

  def submitted_by_belongs_to_account
    return if submitted_by.blank? || account.blank? || account.users.exists?(id: submitted_by.id)

    errors.add(:submitted_by_id, 'must belong to the same account as the template')
  end
end
