# With OS_MANAGED on (ORM appendix code change 9; F11, F12):
# - the clean-up after a removal does nothing if the person is a member again, so it
#   cannot strip the inboxes and teams of a re-added agent;
# - once the person has no account left, every session ends and the API token rotates.
module Custom::Agents::DestroyJob
  def perform(account, user)
    return super unless DurianOs::Config.managed?
    return if account.account_users.exists?(user_id: user.id)

    super
    DurianOs::Sessions.end_all!(user) if user.account_users.reload.empty?
  end
end
