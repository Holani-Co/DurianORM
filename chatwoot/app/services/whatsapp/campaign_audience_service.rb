class Whatsapp::CampaignAudienceService
  E164_PHONE_NUMBER = /\A\+[1-9]\d{1,14}\z/

  Result = Data.define(:contact, :phone_number, :consent, :skip_reason) do
    def eligible?
      skip_reason.blank?
    end
  end

  def initialize(account:, inbox:, audience:)
    @account = account
    @inbox = inbox
    @audience = audience || []
  end

  def results
    @results ||= contacts.map { |contact| build_result(contact) }
  end

  def summary
    reasons = results.filter_map(&:skip_reason).tally
    {
      audience_count: results.size,
      eligible_count: results.count(&:eligible?),
      skipped_count: results.count { |result| !result.eligible? },
      reasons: reasons
    }
  end

  private

  def contacts
    label_ids = @audience.filter_map { |item| item['id'] if item['type'] == 'Label' }
    labels = @account.labels.where(id: label_ids).pluck(:title)
    return @account.contacts.none if labels.empty?

    @account.contacts.tagged_with(labels, any: true).distinct
  end

  def build_result(contact)
    consent = WhatsappConsent.current_for(inbox: @inbox, contact: contact)
    Result.new(
      contact: contact,
      phone_number: contact.phone_number,
      consent: consent,
      skip_reason: skip_reason(contact, consent)
    )
  end

  def skip_reason(contact, consent)
    return 'blocked_contact' if contact.blocked?
    return 'missing_phone_number' if contact.phone_number.blank?
    return 'invalid_phone_number' unless contact.phone_number.match?(E164_PHONE_NUMBER)
    return 'missing_consent' if consent.blank?
    return 'opted_out' unless consent.opted_in?

    nil
  end
end
