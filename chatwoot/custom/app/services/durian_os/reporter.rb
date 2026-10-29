# Change reports to Durian OS (contract section 6, 3.6; ORM appendix code change 10).
# A report names what changed, never what it changed to: Durian OS re-reads the person.
# Reports are queued after the commit; there is no outbox table, so a report lost
# before it is queued is covered by Durian OS's 10-minute check (recorded deviation).
module DurianOs::Reporter
  module_function

  def report_person(kind, user_id)
    return unless DurianOs::Config.reporter_enabled?

    snapshot = DurianOs::AccessSnapshot.new(user_id)
    change = {
      kind: kind,
      localUserId: user_id.to_s,
      osPersonId: snapshot.user&.os_person_id,
      version: snapshot.version,
      actor: actor,
      at: Time.now.utc.iso8601
    }.compact
    report_changes([change])
  end

  def ping
    report_changes([{ kind: 'ping', at: Time.now.utc.iso8601 }])
  end

  def report_changes(changes)
    return unless DurianOs::Config.reporter_enabled?

    DurianOs::ChangeReportJob.perform_later(build_body(changes))
  rescue StandardError => e
    # Never break the write that triggered the report; the 10-minute check catches it.
    Rails.logger.error("[DurianOS] could not queue change report: #{e.class}")
  end

  def build_body(changes)
    {
      reportId: SecureRandom.uuid,
      app: DurianOs::Config::APP_KEY,
      sentAt: Time.now.utc.iso8601,
      changes: changes
    }.to_json
  end

  # X-OS-Signature: v1=<hex HMAC-SHA256 of "v1:{app}:{keyId}:{timestamp}:{hex sha256(body)}">
  def signature(body, timestamp, key: DurianOs::Config.report_key, key_id: DurianOs::Config.report_key_id, app: DurianOs::Config::APP_KEY)
    message = "v1:#{app}:#{key_id}:#{timestamp}:#{Digest::SHA256.hexdigest(body)}"
    "v1=#{OpenSSL::HMAC.hexdigest('SHA256', key, message)}"
  end

  def headers(body, timestamp)
    {
      'Content-Type' => 'application/json',
      'X-OS-App' => DurianOs::Config::APP_KEY,
      'X-OS-Key-Id' => DurianOs::Config.report_key_id,
      'X-OS-Timestamp' => timestamp.to_s,
      'X-OS-Signature' => signature(body, timestamp)
    }
  end

  def actor
    user = Current.user
    return { via: 'app_system' } unless user.is_a?(User)
    return { localUserId: user.id.to_s, email: nil, via: 'emergency_login' } if user.durian_os_emergency?

    { localUserId: user.id.to_s, email: user.email, via: 'app_admin' }
  end
end
