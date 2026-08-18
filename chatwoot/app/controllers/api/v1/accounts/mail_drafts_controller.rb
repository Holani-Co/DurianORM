# Durian — CRUD for saved (unsent) email drafts from the compose flow. Backs the
# left-nav "Drafts" folder. Team-shared: every agent in the account sees the
# same drafts, so no per-user scoping and no admin gate (agents compose email).
class Api::V1::Accounts::MailDraftsController < Api::V1::Accounts::BaseController
  before_action :fetch_draft, only: [:update, :destroy]

  def index
    @mail_drafts = Current.account.mail_drafts.latest_first
  end

  def create
    @mail_draft = Current.account.mail_drafts.create!(draft_params.merge(user: Current.user))
  end

  def update
    @mail_draft.update!(draft_params.merge(user: Current.user))
  end

  def destroy
    @mail_draft.destroy!
    head :ok
  end

  private

  def fetch_draft
    @mail_draft = Current.account.mail_drafts.find(params[:id])
  end

  def draft_params
    params.permit(:inbox_id, :subject, :content,
                  to_emails: [], cc_emails: [], bcc_emails: [])
  end
end
