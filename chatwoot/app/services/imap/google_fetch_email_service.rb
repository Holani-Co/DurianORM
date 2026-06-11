class Imap::GoogleFetchEmailService < Imap::BaseFetchEmailService
  # Custom header used to smuggle Gmail's thread id (an IMAP FETCH
  # attribute, not part of the RFC822 content) through the Mail object to
  # Imap::ImapMailbox, which persists it on the conversation. The
  # dashboard's "Open in Gmail" button uses it to deep-link straight into
  # the thread (mail.google.com/...#all/<hex id>) instead of landing on an
  # rfc822msgid: search-results page.
  GM_THRID_HEADER = 'X-Chatwoot-Gm-Thrid'.freeze

  def fetch_emails
    return if channel.provider_config['access_token'].blank?

    fetch_mail_for_channel
  end

  private

  def authentication_type
    'XOAUTH2'
  end

  def imap_password
    Google::RefreshOauthTokenService.new(channel: channel).access_token
  end

  # Gmail's IMAP extension exposes X-GM-THRID (the thread id Gmail's web
  # UI uses in its URLs) as a fetch attribute. Fetched in a SEPARATE call
  # from the RFC822 body and wrapped fully in rescue: if net-imap or
  # Gmail ever balks at the extension attribute, ingestion continues
  # exactly as before and the frontend falls back to the search link.
  def augment_inbound_mail(inbound_mail, seq_no)
    data = imap_client.fetch(seq_no, 'X-GM-THRID')&.first
    thrid = data&.attr&.[]('X-GM-THRID')
    return if thrid.blank?

    # Stored as STRING everywhere: the id is a 64-bit integer that
    # overflows JS Number precision if it ever travels as JSON numeric.
    inbound_mail.header[GM_THRID_HEADER] = thrid.to_s
  rescue StandardError => e
    Rails.logger.warn("[IMAP::FETCH_EMAIL_SERVICE] X-GM-THRID fetch failed " \
                      "for #{channel.email} seq <#{seq_no}>: #{e.message}")
  end
end
