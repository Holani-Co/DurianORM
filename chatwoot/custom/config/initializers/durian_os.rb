# Durian OS start-up checks and the worker's start-up ping (contract 3.6, 6.3).
Rails.application.config.after_initialize do
  # Refuse to start with a report key that does not decode to exactly 32 bytes.
  DurianOs::Config.validate!

  # Audit rows written while a View as session is used name the admin (shared View as spec 3).
  Audited::Audit.before_create { |audit| DurianOs::ViewAsLog.mark_audit(audit) }
end

Sidekiq.configure_server do |config|
  config.on(:startup) do
    DurianOs::PingJob.perform_later
  rescue StandardError => e
    Rails.logger.error("[DurianOS] start-up ping not queued: #{e.class}")
  end
end
