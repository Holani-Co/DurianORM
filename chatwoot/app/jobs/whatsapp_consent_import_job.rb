# Bulk WhatsApp marketing opt-in via CSV.
#
# One upload → for each row (phone_number[, name]): upsert the Contact, tag it
# with the chosen Label, and record a MARKETING OPTED_IN consent for the chosen
# WhatsApp inbox — so a campaign against that label has an eligible audience
# immediately.
#
# Safeguards:
#   - phone must be E.164 (the campaign audience skips anything else); invalid
#     rows go to a downloadable failed-records CSV.
#   - a contact whose LATEST consent is OPTED_OUT is NOT re-opted-in (a CSV must
#     never override a customer's STOP); those rows are reported too.
class WhatsappConsentImportJob < ApplicationJob
  queue_as :low
  retry_on ActiveStorage::FileNotFoundError, wait: 1.minute, attempts: 3

  E164 = Whatsapp::CampaignAudienceService::E164_PHONE_NUMBER
  CONSENT_BATCH = 1000

  def perform(data_import)
    @data_import = data_import
    @account = data_import.account
    @inbox = @account.inboxes.find_by(id: data_import.inbox_id)
    @contact_manager = DataImport::ContactManager.new(@account)

    return fail_import! if @inbox.nil? || @inbox.channel_type != 'Channel::Whatsapp'

    process
  rescue CSV::MalformedCSVError
    fail_import!
  end

  private

  def process
    @data_import.update!(status: :processing)

    accepted, rejected = parse_rows
    import_contacts(accepted)
    contacts_by_phone = resolve_contacts(accepted)

    tag_contacts(contacts_by_phone.values)
    opted_in = record_optins(contacts_by_phone, rejected)

    save_failed_records(rejected)
    @data_import.update!(status: :completed, processed_records: opted_in, total_records: opted_in + rejected.length)
    notify_admin
  end

  # ── parse ──────────────────────────────────────────────────────────────────
  def parse_rows
    accepted = []
    rejected = []
    with_import_file do |file|
      csv_reader(file).each do |row|
        phone = normalize_phone(row['phone_number'] || row['phone'])
        if phone.blank? || !phone.match?(E164)
          rejected << reject_row(row, 'invalid_phone_number')
          next
        end
        accepted << { phone: phone, name: row['name'].to_s.strip.presence }
      end
    end
    accepted.uniq! { |r| r[:phone] }
    [accepted, rejected]
  end

  def normalize_phone(raw)
    digits = raw.to_s.strip
    return '' if digits.blank?

    digits.start_with?('+') ? digits.gsub(/\s+/, '') : "+#{digits.gsub(/\D/, '')}"
  end

  # ── contacts ─────────────────────────────────────────────────────────────
  def import_contacts(accepted)
    contacts = accepted.map do |row|
      @contact_manager.build_contact({ phone_number: row[:phone], name: row[:name] }.compact)
    end
    return if contacts.blank?

    Contact.import(contacts, on_duplicate_key_ignore: true, validate: true, batch_size: 1000)
  end

  def resolve_contacts(accepted)
    @account.contacts.where(phone_number: accepted.pluck(:phone)).index_by(&:phone_number)
  end

  def tag_contacts(contacts)
    return if contacts.blank? || @data_import.label.blank?

    tag = ActsAsTaggableOn::Tag.find_or_create_all_with_like_by_name(@data_import.label).first
    return if tag.nil?

    rows = contacts.map { |c| [tag.id, 'Contact', c.id, 'labels', Time.current] }
    ActsAsTaggableOn::Tagging.import(%i[tag_id taggable_type taggable_id context created_at],
                                     rows, on_duplicate_key_ignore: true, validate: false, batch_size: 1000)
  end

  # ── consent ──────────────────────────────────────────────────────────────
  def record_optins(contacts_by_phone, rejected)
    contacts = contacts_by_phone.values
    return 0 if contacts.blank?

    blocked = opted_out_contact_ids(contacts.map(&:id))
    # Respect standing opt-outs: a CSV must never re-opt-in someone who replied STOP.
    eligible, skipped = contacts.partition { |c| blocked.exclude?(c.id) }
    skipped.each { |c| rejected << reject_row({ 'phone_number' => c.phone_number }, 'opted_out') }
    insert_consents(eligible)
    eligible.length
  end

  def insert_consents(eligible)
    now = Time.current
    eligible.each_slice(CONSENT_BATCH) do |batch|
      # rubocop:disable Rails/SkipsModelValidations
      WhatsappConsent.insert_all(batch.map { |c| consent_row(c, now) }, unique_by: :idx_wa_consents_source_reference)
      # rubocop:enable Rails/SkipsModelValidations
    end
  end

  # Contacts whose CURRENT (latest) MARKETING consent for this inbox is OPTED_OUT.
  def opted_out_contact_ids(contact_ids)
    WhatsappConsent
      .where(inbox: @inbox, contact_id: contact_ids, purpose: 'MARKETING')
      .select('DISTINCT ON (contact_id) contact_id, status')
      .order(:contact_id, recorded_at: :desc, id: :desc)
      .select { |c| c.status == 'OPTED_OUT' }
      .map(&:contact_id)
  end

  def consent_row(contact, now)
    {
      account_id: @account.id,
      inbox_id: @inbox.id,
      contact_id: contact.id,
      purpose: 'MARKETING',
      status: 'OPTED_IN',
      source: 'csv_import',
      source_reference: "data_import:#{@data_import.id}:#{contact.id}",
      recorded_at: now,
      details: { data_import_id: @data_import.id },
      created_at: now,
      updated_at: now
    }
  end

  # ── failed records + status ──────────────────────────────────────────────
  def reject_row(row, reason)
    { 'phone_number' => (row['phone_number'] || row['phone']).to_s, 'name' => row['name'].to_s, 'errors' => reason }
  end

  def save_failed_records(rejected)
    return if rejected.blank?

    csv_data = CSV.generate do |csv|
      csv << %w[phone_number name errors]
      rejected.each { |r| csv << [safe_csv(r['phone_number']), safe_csv(r['name']), r['errors']] }
    end
    @data_import.failed_records.attach(io: StringIO.new(csv_data),
                                       filename: "#{Time.zone.today.strftime('%Y%m%d')}_whatsapp_optin.csv",
                                       content_type: 'text/csv')
  end

  def safe_csv(value)
    string = value.to_s
    string.match?(/\A[=+\-@]/) ? "'#{string}" : string
  end

  def fail_import!
    @data_import.update!(status: :failed)
    AdministratorNotifications::AccountNotificationMailer.with(account: @account).contact_import_failed.deliver_later
  end

  def notify_admin
    AdministratorNotifications::AccountNotificationMailer.with(account: @account).contact_import_complete(@data_import).deliver_later
  end

  # ── CSV helpers (mirror DataImportJob) ────────────────────────────────────
  def csv_reader(file)
    file.rewind
    raw_data = file.read.force_encoding('UTF-8')
    clean_data = raw_data.valid_encoding? ? raw_data : raw_data.encode('UTF-16le', invalid: :replace, replace: '').encode('UTF-8')
    CSV.new(StringIO.new(clean_data.delete_prefix("\xEF\xBB\xBF")), headers: true)
  end

  def with_import_file
    temp_dir = Rails.root.join('tmp/imports')
    FileUtils.mkdir_p(temp_dir)
    @data_import.import_file.open(tmpdir: temp_dir) do |file|
      file.binmode
      yield file
    end
  end
end
