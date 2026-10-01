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

    raise Error, media_mismatch_message unless valid_content_type?
    raise Error, 'Campaign media must be smaller than 16 MB' if @blob.byte_size > MAX_FILE_SIZE
  end

  private

  # A WhatsApp template's header type (IMAGE/VIDEO/DOCUMENT) is fixed at approval,
  # so an image template can't carry a video and vice-versa. Reject up front with a
  # clear message instead of letting a mismatched file upload to Meta and reach the
  # customer as a broken "something wrong with the video file" message.
  def media_mismatch_message
    allowed = CONTENT_TYPES.fetch(media_format, []).map { |t| t.split('/').last.upcase }.join('/')
    "This template has a #{media_format.upcase} header — the file you uploaded is " \
      "not a #{media_format} (#{detected_content_type}). Upload a #{media_format} " \
      "(#{allowed}), or pick a template whose header matches your file."
  end

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

  # Match BOTH the declared content type AND the type sniffed from the file's own
  # bytes — a file renamed/mislabelled (e.g. an .mp4 served as image/jpeg) passes
  # the declared check but is caught by the sniff.
  def valid_content_type?
    allowed = CONTENT_TYPES.fetch(media_format, [])
    # The declared type must match the header (original rule — unchanged).
    return false unless allowed.include?(@blob.content_type)

    # Then, only REJECT when the file's own bytes clearly say a different, KNOWN
    # media type (the mislabelled-video case). Inconclusive sniffing (blank /
    # octet-stream) or agreement with the declared type is trusted → no new false
    # negatives for legitimate files.
    sniffed = detected_content_type
    return true if sniffed.blank? || sniffed == @blob.content_type || sniffed == 'application/octet-stream'

    allowed.include?(sniffed)
  end

  # True media type from the first few KB of the file (magic bytes). Cheap: only a
  # small range is downloaded, so this is safe even on the per-recipient send path.
  # Falls back to the declared type if sniffing fails (no worse than before).
  def detected_content_type
    @detected_content_type ||= begin
      head = @blob.download(range: 0...(4.kilobytes))
      Marcel::MimeType.for(StringIO.new(head.to_s),
                           name: @blob.filename.to_s,
                           declared_type: @blob.content_type)
    end
  rescue StandardError
    @blob.content_type
  end
end
