# With OS_MANAGED on (ORM appendix code change 9; F2, F22):
# - Remove agent removes the membership only and keeps the user row, which Durian OS
#   links by os_person_id. Sessions and the API token are ended once no account is left
#   (Agents::DestroyJob).
# - Service and emergency rows cannot be edited or removed here.
module Custom::Api::V1::Accounts::AgentsController
  def self.prepended(base)
    base.before_action :durian_os_refuse_protected_rows, only: [:update, :destroy]
  end

  private

  def durian_os_refuse_protected_rows
    return unless DurianOs::Config.managed? && @agent&.durian_os_protected?

    render json: { error: 'This account is managed by Durian OS and cannot be changed here.' }, status: :unprocessable_entity
  end

  def delete_user_record(agent)
    return if DurianOs::Config.managed?

    super
  end
end
