module Custom::Concerns::AccountUser
  extend ActiveSupport::Concern
  include DurianOs::Reportable

  private

  def durian_os_change_kind
    return 'person.deactivated' if durian_os_destroyed?
    return 'person.created' if durian_os_created?

    'person.changed' if saved_change_to_role? || saved_change_to_custom_role_id?
  end

  def durian_os_account_id
    account_id
  end
end
