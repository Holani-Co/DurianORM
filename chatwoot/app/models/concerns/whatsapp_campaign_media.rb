module WhatsappCampaignMedia
  extend ActiveSupport::Concern

  MEDIA_HEADER_FORMATS = %w[image video document].freeze

  included do
    validate :whatsapp_media_matches_template
    validate :whatsapp_media_required_for_template
  end

  def whatsapp_media_params(template_parameters)
    # The local blob is purged once a campaign completes, but the Meta media id
    # persists — so a retry can still attach the header by id without the file.
    return template_parameters unless media.attached? || whatsapp_media_id.present?

    blob = media.attached? ? media.blob : nil
    Whatsapp::CampaignMediaService.new(blob: blob, template: whatsapp_template, media_id: whatsapp_media_id).apply(template_parameters)
  end

  private

  def whatsapp_media_matches_template
    return unless media.attached?

    Whatsapp::CampaignMediaService.new(blob: media.blob, template: whatsapp_template).validate!
  rescue Whatsapp::CampaignMediaService::Error => e
    errors.add(:media, e.message)
  end

  # A template with an image/video/document header cannot be sent without media
  # (Meta rejects it as error 132012 "Format mismatch, received UNKNOWN"). Require
  # either an attached file or a public media URL before the campaign can launch.
  def whatsapp_media_required_for_template
    header_format = whatsapp_template_media_format
    return if header_format.blank?
    return if media.attached?
    # Already uploaded to Meta (e.g. a completed campaign being retried after its
    # local blob was purged) — the media id is a valid source.
    return if whatsapp_media_id.present?
    return if (template_params&.dig('processed_params', 'header') || {})['media_url'].present?

    msg = "This template has a #{header_format} header — upload a file or provide a public " \
          "#{header_format} URL before launching the campaign."
    errors.add(:media, msg)
  end

  def whatsapp_template_media_format
    format = Array(whatsapp_template&.components)
             .find { |component| component['type'].to_s.upcase == 'HEADER' }&.dig('format')&.to_s&.downcase
    format if MEDIA_HEADER_FORMATS.include?(format)
  end
end
