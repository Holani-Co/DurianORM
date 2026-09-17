class Whatsapp::CampaignMediaService
  class Error < StandardError; end

  MAX_FILE_SIZE = 16.megabytes
  CONTENT_TYPES = {
    'image' => %w[image/jpeg image/png],
    'video' => %w[video/mp4],
    'document' => %w[application/pdf]
  }.freeze

  def initialize(blob:, template:, media_id: nil)
    @blob = blob
    @template = template
    @media_id = media_id
  end

  # Uploads the blob to Meta once and captures the returned media id so sends can
  # reference it by id instead of a public URL.
  def upload_to!(channel)
    validate!
    @media_id = channel.upload_media(@blob)
  end

  def apply(template_params)
    validate!

    params = template_params.deep_dup
    params['processed_params'] ||= {}
    header = params['processed_params']['header'] ||= {}
    header['media_type'] ||= media_format
    apply_media_source(header)
    header['media_name'] = @blob.filename.to_s if document_name_needed?(header)
    params
  end

  def apply_media_source(header)
    if @media_id.present?
      header['media_id'] = @media_id
      header.delete('media_url')
    else
      header['media_url'] = download_url
    end
  end

  def document_name_needed?(header)
    media_format == 'document' && @blob.present? && header['media_name'].blank?
  end

  def validate!
    raise Error, 'The selected template does not have a media header' if media_format.blank?
    # A retry sends by an already-uploaded Meta media id; the local blob is gone,
    # so there is no file to size/type-check.
    return if @blob.nil?

    raise Error, "Upload a #{media_format} file that matches the template header" unless valid_content_type?
    raise Error, 'Campaign media must be smaller than 16 MB' if @blob.byte_size > MAX_FILE_SIZE
  end

  private

  def download_url
    ActiveStorage::Current.url_options ||= Rails.application.routes.default_url_options
    @blob.url
  end

  def media_format
    @media_format ||= Array(@template&.components)
                      .find { |component| component['type'].to_s.upcase == 'HEADER' }
                      &.dig('format')
                      &.downcase
  end

  def valid_content_type?
    CONTENT_TYPES.fetch(media_format, []).include?(@blob.content_type)
  end
end
