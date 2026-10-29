# One Chatwoot user per Durian OS person (ORM appendix code change 13; F14).
# Additive: an index on users.custom_attributes->>'os_person_id', only where it is set.
class AddOsPersonIdIndexToUsers < ActiveRecord::Migration[7.1]
  disable_ddl_transaction!

  def change
    add_index :users, "((custom_attributes ->> 'os_person_id'::text))",
              name: 'index_users_on_os_person_id',
              unique: true,
              where: "((custom_attributes ->> 'os_person_id'::text) IS NOT NULL)",
              algorithm: :concurrently
  end
end
