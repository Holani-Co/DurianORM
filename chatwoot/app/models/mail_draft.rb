# Durian — an unsent email composed via the "Compose new conversation" flow and
# saved to send later. Shown in the left-nav "Drafts" folder; resuming one
# reopens the composer pre-filled, and sending it creates the conversation
# (marked Sent) and deletes the draft. Team-shared (account-scoped).
# == Schema Information
#
# Table name: mail_drafts
#
#  id         :bigint           not null, primary key
#  bcc_emails :jsonb            not null
#  cc_emails  :jsonb            not null
#  content    :text
#  subject    :string
#  to_emails  :jsonb            not null
#  created_at :datetime         not null
#  updated_at :datetime         not null
#  account_id :bigint           not null
#  inbox_id   :bigint
#  user_id    :bigint
#
# Indexes
#
#  index_mail_drafts_on_account_id  (account_id)
#  index_mail_drafts_on_inbox_id    (inbox_id)
#  index_mail_drafts_on_user_id     (user_id)
#
class MailDraft < ApplicationRecord
  belongs_to :account
  belongs_to :inbox, optional: true
  belongs_to :user, optional: true

  validates :account_id, presence: true

  scope :latest_first, -> { order(updated_at: :desc) }
end
