class WhatsappTemplatePolicy < ApplicationPolicy
  def index?
    @account_user.administrator?
  end

  def show?
    @account_user.administrator?
  end

  def create?
    @account_user.administrator?
  end

  def update?
    @account_user.administrator?
  end

  def destroy?
    @account_user.administrator?
  end

  def submit?
    @account_user.administrator?
  end

  def sync?
    @account_user.administrator?
  end

  def upload_sample?
    @account_user.administrator?
  end
end
