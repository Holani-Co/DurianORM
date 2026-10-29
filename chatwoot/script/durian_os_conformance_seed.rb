# frozen_string_literal: true

# Prepares a LOCAL development database for script/durian_os_conformance.rb.
#   RAILS_ENV=development bundle exec rails runner script/durian_os_conformance_seed.rb
# Creates (or reuses) account 1, a test administrator, the emergency user and a Platform App
# with rights over both users, then writes throwaway values to tmp/durian_os_conformance.env
# (git-ignored, mode 0600). Nothing is printed. Refuses to run outside development/test.

abort 'Refusing to run outside development or test.' unless Rails.env.development? || Rails.env.test?
db_host = ActiveRecord::Base.connection_db_config.configuration_hash[:host].to_s
abort 'Refusing to run against a non-local database.' unless %w[localhost 127.0.0.1 host.docker.internal].include?(db_host)

account = Account.find_by(id: DurianOs::Config.account_id) || Account.create!(name: 'Durian Local')

def local_user(email, name, attributes = {})
  user = User.from_email(email) || User.new(email: email, name: name)
  user.password = "#{SecureRandom.hex(12)}aA1!"
  user.custom_attributes = (user.custom_attributes || {}).merge(attributes)
  user.skip_confirmation!
  user.save!
  user
end

admin = local_user('conformance-admin@orm.test', 'Conformance Admin')
emergency = local_user(ENV.fetch('OS_EMERGENCY_EMAIL', 'emergency@orm.test'), 'Emergency admin', 'os_kind' => 'emergency')
[admin, emergency].each do |user|
  AccountUser.find_or_create_by!(account: account, user: user) { |au| au.role = :administrator }
end

platform_app = PlatformApp.find_or_create_by!(name: 'Durian OS (local conformance)')
[admin, emergency].each { |user| platform_app.platform_app_permissibles.find_or_create_by!(permissible: user) }

values = {
  'ORM_PLATFORM_TOKEN' => platform_app.access_token.token,
  'ORM_TEST_USER_ID' => admin.id.to_s,
  'ORM_EMERGENCY_EMAIL' => emergency.email,
  'OS_REPORT_KEY' => ENV.fetch('OS_REPORT_KEY') { Base64.urlsafe_encode64(SecureRandom.random_bytes(32), padding: false) },
  'OS_REPORT_KEY_ID' => ENV.fetch('OS_REPORT_KEY_ID', 'orm-local')
}
path = Rails.root.join('tmp/durian_os_conformance.env')
File.write(path, values.map { |k, v| "export #{k}=#{v}\n" }.join)
File.chmod(0o600, path)
