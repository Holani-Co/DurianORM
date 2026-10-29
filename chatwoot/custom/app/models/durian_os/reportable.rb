# Hooks AccountUser, InboxMember and TeamMember so every write path (agents page,
# inbox and team settings, super admin, Platform API, SAML, jobs) sends a change
# report for the managed account (ORM appendix code change 10).
# Including models define durian_os_change_kind and durian_os_account_id.
module DurianOs::Reportable
  extend ActiveSupport::Concern

  included do
    after_commit :durian_os_report_change, on: [:create, :update, :destroy]
  end

  private

  def durian_os_report_change
    return unless DurianOs::Config.reporter_enabled?

    kind = durian_os_change_kind
    return if kind.nil? || durian_os_account_id != DurianOs::Config.account_id

    DurianOs::Reporter.report_person(kind, user_id)
  rescue StandardError => e
    Rails.logger.error("[DurianOS] change report skipped: #{e.class}")
  end

  def durian_os_destroyed?
    destroyed?
  end

  def durian_os_created?
    previously_new_record?
  end

  # When the inbox or team is already gone, fall back to the person's membership.
  def durian_os_fallback_account_id
    managed = DurianOs::Config.account_id
    managed if AccountUser.exists?(account_id: managed, user_id: user_id)
  end
end
