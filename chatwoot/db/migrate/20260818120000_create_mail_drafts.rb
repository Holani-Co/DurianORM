# Durian — saved (unsent) email drafts for the compose flow. A draft has no
# conversation yet (that's created only on send), so it needs its own store.
# Team-shared: any agent in the account sees and can resume the drafts.
class CreateMailDrafts < ActiveRecord::Migration[7.1]
  def change
    create_table :mail_drafts do |t|
      t.references :account, null: false, index: true
      t.references :inbox, null: true
      t.references :user, null: true # who last saved it (audit only; drafts are shared)
      t.jsonb :to_emails, null: false, default: []
      t.jsonb :cc_emails, null: false, default: []
      t.jsonb :bcc_emails, null: false, default: []
      t.string :subject
      t.text :content
      t.timestamps
    end
  end
end
