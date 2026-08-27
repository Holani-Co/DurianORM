class Whatsapp::TemplateManagementService
  class Error < StandardError; end

  API_VERSION = 'v22.0'.freeze
  MAX_SAMPLE_SIZE = 16.megabytes
  SAMPLE_CONTENT_TYPES = %w[image/jpeg image/png video/mp4 application/pdf].freeze

  def initialize(inbox)
    @inbox = inbox
    @channel = inbox.channel
    validate_channel!
  end

  def submit!(template)
    raise Error, 'Only draft or rejected templates can be submitted' unless %w[DRAFT REJECTED].include?(template.status)

    response = HTTParty.post(message_templates_path, headers: api_headers, body: submission_payload(template).to_json)
    raise_api_error!(response, 'Template submission failed') unless response.success?

    template.update!(
      meta_template_id: response['id'],
      status: response['status'].presence || 'PENDING',
      rejection_reason: nil,
      submitted_at: Time.current,
      rejected_at: nil
    )
    template
  end

  def sync!
    templates = fetch_templates(message_templates_path)
    now = Time.current

    WhatsappTemplate.transaction do
      templates.each { |template| import_template!(template, now) }
      @channel.update!(message_templates: templates, message_templates_last_updated: now)
    end

    @inbox.whatsapp_templates.reload.latest_first
  end

  def update!(template)
    raise Error, 'Template has not been submitted to Meta' if template.meta_template_id.blank?
    raise Error, template.errors.full_messages.to_sentence unless template.valid?

    response = HTTParty.post(
      "#{api_base_path}/#{api_version}/#{template.meta_template_id}",
      headers: api_headers,
      body: submission_payload(template).to_json
    )
    raise_api_error!(response, 'Template update failed') unless response.success?

    template.update!(
      status: response['status'].presence || 'PENDING',
      rejection_reason: nil,
      submitted_at: Time.current,
      rejected_at: nil
    )
    template
  end

  def delete!(template)
    delete_from_meta!(template) if template.meta_template_id.present?
    template.destroy!
  end

  def upload_sample!(file)
    validate_sample!(file)
    session = create_upload_session(file)
    response = HTTParty.post(
      "#{api_base_path}/#{api_version}/#{session.fetch('id')}",
      headers: api_headers.merge('file_offset' => '0', 'Content-Type' => file.content_type),
      body: file.read
    )
    raise_api_error!(response, 'Media sample upload failed') unless response.success?

    response['h'].presence || raise(Error, 'Meta did not return a media handle')
  ensure
    file.rewind if file.respond_to?(:rewind)
  end

  private

  def validate_channel!
    return if @channel.is_a?(Channel::Whatsapp) && @channel.provider == 'whatsapp_cloud'

    raise Error, 'Template management is only available for WhatsApp Cloud API inboxes'
  end

  def validate_sample!(file)
    raise Error, 'Unsupported media type' unless SAMPLE_CONTENT_TYPES.include?(file.content_type)
    raise Error, 'Media sample must be smaller than 16 MB' if file.size > MAX_SAMPLE_SIZE
  end

  def create_upload_session(file)
    app_id = GlobalConfigService.load('WHATSAPP_APP_ID', nil)
    raise Error, 'WhatsApp App ID is not configured' if app_id.blank?

    response = HTTParty.post(
      "#{api_base_path}/#{api_version}/#{app_id}/uploads",
      headers: api_headers,
      query: { file_length: file.size, file_type: file.content_type, file_name: file.original_filename }
    )
    raise_api_error!(response, 'Could not create a media upload session') unless response.success?

    response.parsed_response
  end

  def submission_payload(template)
    {
      name: template.name,
      language: template.language,
      category: template.category,
      components: template.components
    }
  end

  def fetch_templates(url)
    response = HTTParty.get(url, headers: api_headers)
    raise_api_error!(response, 'Template sync failed') unless response.success?

    templates = response['data'] || []
    next_url = response.dig('paging', 'next')
    next_url.present? ? templates + fetch_templates(next_url) : templates
  end

  def import_template!(meta_template, synced_at)
    template = @inbox.whatsapp_templates.find_or_initialize_by(
      name: meta_template['name'],
      language: meta_template['language']
    )
    status = meta_template['status'].presence || 'PENDING'

    template.assign_attributes(
      account: @inbox.account,
      meta_template_id: meta_template['id'],
      category: meta_template['category'],
      status: status,
      components: meta_template['components'] || [],
      quality_rating: meta_template['quality_score']&.dig('score') || meta_template['quality_rating'],
      rejection_reason: meta_template['rejected_reason'],
      last_synced_at: synced_at
    )
    apply_status_timestamps(template, status, synced_at)
    template.save!
  end

  def apply_status_timestamps(template, status, timestamp)
    template.approved_at ||= timestamp if status == 'APPROVED'
    template.rejected_at ||= timestamp if status == 'REJECTED'
  end

  def delete_from_meta!(template)
    query = { name: template.name, hsm_id: template.meta_template_id }.to_query
    response = HTTParty.delete("#{message_templates_path}?#{query}", headers: api_headers)
    raise_api_error!(response, 'Template deletion failed') unless response.success?
  end

  def raise_api_error!(response, fallback)
    response_body = response.parsed_response
    message = response_body.is_a?(Hash) ? response_body.dig('error', 'message') : nil
    raise Error, message || fallback
  end

  def message_templates_path
    "#{api_base_path}/#{api_version}/#{@channel.provider_config['business_account_id']}/message_templates"
  end

  def api_base_path
    ENV.fetch('WHATSAPP_CLOUD_BASE_URL', 'https://graph.facebook.com')
  end

  def api_version
    GlobalConfigService.load('WHATSAPP_API_VERSION', API_VERSION)
  end

  def api_headers
    {
      'Authorization' => "Bearer #{@channel.provider_config['api_key']}",
      'Content-Type' => 'application/json'
    }
  end
end
