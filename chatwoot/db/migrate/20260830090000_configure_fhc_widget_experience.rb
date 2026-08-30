class ConfigureFhcWidgetExperience < ActiveRecord::Migration[7.1]
  WEBSITE_INBOX_NAMES = ['Website Bot', 'Durian FHC'].freeze

  def up
    replace_config('INSTALLATION_NAME', 'Chatwoot', 'MiracleAI')
    replace_config('BRAND_NAME', 'Chatwoot', 'MiracleAI')
    replace_config('WIDGET_BRAND_URL', 'https://www.chatwoot.com', 'https://orm.durianos.in')

    website_inboxes.find_each do |inbox|
      inbox.update!(enable_email_collect: false, allow_messages_after_resolved: true)
    end
  end

  def down
    replace_config('INSTALLATION_NAME', 'MiracleAI', 'Chatwoot')
    replace_config('BRAND_NAME', 'MiracleAI', 'Chatwoot')
    replace_config('WIDGET_BRAND_URL', 'https://orm.durianos.in', 'https://www.chatwoot.com')

    website_inboxes.find_each { |inbox| inbox.update!(enable_email_collect: true) }
  end

  private

  def replace_config(name, old_value, new_value)
    config = InstallationConfig.find_by(name: name)
    return unless config&.value == old_value

    config.value = new_value
    config.save!
  end

  def website_inboxes
    Inbox.where(channel_type: 'Channel::WebWidget', name: WEBSITE_INBOX_NAMES)
  end
end
