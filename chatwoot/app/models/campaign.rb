# == Schema Information
#
# Table name: campaigns
#
#  id                                 :bigint           not null, primary key
#  audience                           :jsonb
#  audience_count                     :integer          default(0), not null
#  audience_snapshot_at               :datetime
#  campaign_status                    :integer          default("active"), not null
#  campaign_type                      :integer          default("ongoing"), not null
#  delivered_count                    :integer          default(0), not null
#  description                        :text
#  eligible_count                     :integer          default(0), not null
#  enabled                            :boolean          default(TRUE)
#  execution_completed_at             :datetime
#  execution_error                    :text
#  execution_started_at               :datetime
#  execution_status                   :integer
#  failed_count                       :integer          default(0), not null
#  message                            :text             not null
#  read_count                         :integer          default(0), not null
#  reply_count                        :integer          default(0), not null
#  scheduled_at                       :datetime
#  sent_count                         :integer          default(0), not null
#  skipped_count                      :integer          default(0), not null
#  template_params                    :jsonb
#  title                              :string           not null
#  trigger_only_during_business_hours :boolean          default(FALSE)
#  trigger_rules                      :jsonb
#  created_at                         :datetime         not null
#  updated_at                         :datetime         not null
#  account_id                         :bigint           not null
#  display_id                         :integer          not null
#  inbox_id                           :bigint           not null
#  sender_id                          :integer
#  whatsapp_template_id               :bigint
#
# Indexes
#
#  idx_campaigns_account_execution_status   (account_id,execution_status)
#  index_campaigns_on_account_id            (account_id)
#  index_campaigns_on_campaign_status       (campaign_status)
#  index_campaigns_on_campaign_type         (campaign_type)
#  index_campaigns_on_inbox_id              (inbox_id)
#  index_campaigns_on_scheduled_at          (scheduled_at)
#  index_campaigns_on_whatsapp_template_id  (whatsapp_template_id)
#
class Campaign < ApplicationRecord
  include UrlHelper

  EXECUTION_TRANSITIONS = {
    'draft' => %w[scheduled cancelled failed],
    'scheduled' => %w[queued paused cancelled failed],
    'queued' => %w[running paused cancelled failed],
    'running' => %w[paused completed cancelled failed],
    'paused' => %w[scheduled queued running cancelled],
    'failed' => %w[scheduled queued cancelled],
    'completed' => [],
    'cancelled' => []
  }.freeze

  validates :account_id, presence: true
  validates :inbox_id, presence: true
  validates :title, presence: true
  validates :message, presence: true
  validate :validate_campaign_inbox
  validate :validate_url
  validate :prevent_completed_campaign_from_update, on: :update
  validate :sender_must_belong_to_account
  validate :inbox_must_belong_to_account
  validate :whatsapp_template_must_match_campaign
  validate :prevent_definition_changes_after_snapshot, on: :update

  belongs_to :account
  belongs_to :inbox
  belongs_to :sender, class_name: 'User', optional: true
  belongs_to :whatsapp_template, optional: true

  enum campaign_type: { ongoing: 0, one_off: 1 }
  # TODO : enabled attribute is unneccessary . lets move that to the campaign status with additional statuses like draft, disabled etc.
  enum campaign_status: { active: 0, completed: 1 }
  enum :execution_status, {
    draft: 0,
    scheduled: 1,
    queued: 2,
    running: 3,
    paused: 4,
    completed: 5,
    cancelled: 6,
    failed: 7
  }, prefix: :execution

  has_many :conversations, dependent: :nullify, autosave: true
  has_many :campaign_deliveries, dependent: :destroy_async

  before_validation :ensure_correct_campaign_attributes
  before_validation :initialize_execution_status
  after_commit :set_display_id, unless: :display_id?

  def trigger!
    return unless one_off?
    return if completed?

    execute_campaign
  end

  def transition_execution_to!(target_status, error: nil)
    target_status = target_status.to_s
    raise ArgumentError, "Unknown campaign execution status: #{target_status}" unless self.class.execution_statuses.key?(target_status)

    with_lock do
      # Idempotent: if we're already in the target state (e.g. two finalizers
      # racing to complete the campaign), do nothing rather than raise.
      next if execution_status.to_s == target_status

      allowed_statuses = EXECUTION_TRANSITIONS.fetch(execution_status.to_s, [])
      unless allowed_statuses.include?(target_status)
        raise ArgumentError, "Cannot transition campaign execution from #{execution_status.inspect} to #{target_status}"
      end

      update!(execution_transition_attributes(target_status, error))
    end
  end

  def execution_terminal?
    execution_completed? || execution_cancelled? || execution_failed?
  end

  def refresh_delivery_counts!
    # Delivery webhooks continue after a campaign is complete, so counters must
    # bypass the completed-campaign definition guard.
    # rubocop:disable Rails/SkipsModelValidations
    update_columns(delivery_counts.merge(updated_at: Time.current))
    # rubocop:enable Rails/SkipsModelValidations
  end

  private

  def execution_transition_attributes(target_status, error)
    attributes = {
      execution_status: target_status,
      execution_completed_at: %w[completed cancelled failed].include?(target_status) ? Time.current : nil,
      execution_error: target_status == 'failed' ? error.to_s.presence : nil
    }
    attributes[:execution_started_at] = Time.current if target_status == 'running' && execution_started_at.blank?
    attributes
  end

  def delivery_counts
    counts = campaign_deliveries.group(:status).count
    {
      audience_count: counts.values.sum,
      eligible_count: counts.values.sum - counts.fetch('skipped', 0),
      skipped_count: counts.fetch('skipped', 0),
      failed_count: counts.fetch('failed', 0)
    }.merge(delivery_engagement_counts)
  end

  def delivery_engagement_counts
    {
      sent_count: campaign_deliveries.where.not(sent_at: nil).where.not(status: 'failed').count,
      delivered_count: campaign_deliveries.where.not(delivered_at: nil).count,
      read_count: campaign_deliveries.where.not(read_at: nil).count,
      reply_count: campaign_deliveries.where.not(replied_at: nil).count
    }
  end

  def initialize_execution_status
    return unless one_off?
    return if execution_status.present?

    self.execution_status = completed? ? :completed : :scheduled
  end

  def execute_campaign
    case inbox.inbox_type
    when 'Twilio SMS'
      Twilio::OneoffSmsCampaignService.new(campaign: self).perform
    when 'Sms'
      Sms::OneoffSmsCampaignService.new(campaign: self).perform
    when 'Whatsapp'
      # No feature-flag guard here: the service's validate_feature_flag! raises
      # when the flag is off, which marks the campaign failed with a visible
      # error instead of silently looping in TriggerScheduledItemsJob forever.
      Whatsapp::OneoffCampaignService.new(campaign: self).perform
    end
  end

  def set_display_id
    reload
  end

  def validate_campaign_inbox
    return unless inbox

    errors.add :inbox, 'Unsupported Inbox type' unless ['Website', 'Twilio SMS', 'Sms', 'Whatsapp'].include? inbox.inbox_type
  end

  # TO-DO we clean up with better validations when campaigns evolve into more inboxes
  def ensure_correct_campaign_attributes
    return if inbox.blank?

    if ['Twilio SMS', 'Sms', 'Whatsapp'].include?(inbox.inbox_type)
      self.campaign_type = 'one_off'
      self.scheduled_at ||= Time.now.utc
    else
      self.campaign_type = 'ongoing'
      self.scheduled_at = nil
    end
  end

  def validate_url
    return unless trigger_rules['url']

    use_http_protocol = trigger_rules['url'].starts_with?('http://') || trigger_rules['url'].starts_with?('https://')
    errors.add(:url, 'invalid') if inbox.inbox_type == 'Website' && !use_http_protocol
  end

  def inbox_must_belong_to_account
    return unless inbox

    return if inbox.account_id == account_id

    errors.add(:inbox_id, 'must belong to the same account as the campaign')
  end

  def sender_must_belong_to_account
    return unless sender

    return if account.users.exists?(id: sender.id)

    errors.add(:sender_id, 'must belong to the same account as the campaign')
  end

  def whatsapp_template_must_match_campaign
    return if whatsapp_template.blank?

    errors.add(:whatsapp_template_id, 'must belong to the same account as the campaign') if whatsapp_template.account_id != account_id
    errors.add(:whatsapp_template_id, 'must belong to the campaign inbox') if whatsapp_template.inbox_id != inbox_id
  end

  def prevent_completed_campaign_from_update
    errors.add :status, 'The campaign is already completed' if !campaign_status_changed? && completed?
  end

  def prevent_definition_changes_after_snapshot
    return if audience_snapshot_at.blank?

    immutable_fields = %w[inbox_id audience template_params whatsapp_template_id scheduled_at]
    definition_changed = immutable_fields.any? { |field| will_save_change_to_attribute?(field) }
    errors.add(:base, 'Campaign definition cannot change after the audience snapshot') if definition_changed
  end

  # creating db triggers
  trigger.before(:insert).for_each(:row) do
    "NEW.display_id := nextval('camp_dpid_seq_' || NEW.account_id);"
  end
end
