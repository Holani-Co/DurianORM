# Logs and audit rows for Durian OS "View as" (shared View as spec 3). Log lines are
# "[DurianOS] view_as.<event> {json}" naming the person and the admin.
module DurianOs::ViewAsLog
  module_function

  def link(user, session)
    Rails.logger.info("[DurianOS] view_as.link #{fields(user, session).to_json}")
  end

  # The sign-in itself: a log line, and an audit row naming both people. With Enterprise
  # on, its own sign_in audit rows are written in the same request and get the comment
  # from mark_audit, so a row is written here only without Enterprise.
  def started(user, session, ip:, audit:)
    Rails.logger.warn("[DurianOS] view_as.started #{fields(user, session).merge(ip: ip.to_s).to_json}")
    return unless audit

    Audited.audit_class.create!(auditable: user, user: user, action: 'sign_in',
                                associated_type: 'Account', associated_id: DurianOs::Config.account_id,
                                comment: started_comment(session, user.email), remote_address: ip.to_s)
  rescue StandardError => e
    Rails.logger.error("[DurianOS] view_as.started audit not written: #{e.class}")
  end

  def request(session, user, request)
    Rails.logger.info("[DurianOS] view_as.request #{request_fields(session, user, request).to_json}")
  end

  def blocked(session, user, request)
    Rails.logger.warn("[DurianOS] view_as.blocked #{request_fields(session, user, request).to_json}")
  end

  # Audit rows written while a View as session is used name the admin in Audited's comment
  # (Enterprise::AuditLog overwrites username with the user's email after save).
  def mark_audit(audit)
    session = DurianOs::Current.view_as
    return if session.nil?

    person = DurianOs::Current.user&.email
    note = if audit.action == 'sign_in'
             started_comment(session, person)
           else
             "#{session.admin_label} viewing as #{person || 'someone'} (imp #{session.imp})"
           end
    audit.comment = [note, audit.comment.presence].compact.join(' · ').first(DurianOs::ViewAs::MAX_TEXT)
  end

  def started_comment(session, person)
    "#{session.admin_label} started viewing as #{person || 'someone'} (imp #{session.imp})".first(DurianOs::ViewAs::MAX_TEXT)
  end

  def request_fields(session, user, request)
    fields(user, session).merge(method: request.request_method, path: request.path)
  end

  def fields(user, session)
    { user_id: user&.id, user_email: user.try(:email), by_email: session.by_email, by_name: session.by_name,
      imp: session.imp, imp_exp: session.imp_exp }
  end
end
