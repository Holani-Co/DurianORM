module Custom::Concerns::InboxMember
  extend ActiveSupport::Concern
  include DurianOs::Reportable

  private

  def durian_os_change_kind
    'person.changed' if durian_os_created? || durian_os_destroyed?
  end

  def durian_os_account_id
    Inbox.where(id: inbox_id).pick(:account_id) || durian_os_fallback_account_id
  end
end
