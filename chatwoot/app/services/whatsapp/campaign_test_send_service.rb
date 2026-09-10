class Whatsapp::CampaignTestSendService
  class Error < StandardError; end

  E164_PHONE_NUMBER = /\A\+[1-9]\d{1,14}\z/

  def initialize(inbox:, template:, phone_number:, template_params:, media_blob: nil)
    @inbox = inbox
    @template = template
    @phone_number = phone_number.to_s.strip
    @template_params = template_params
    @media_blob = media_blob
  end

  def perform
    validate!
    name, namespace, language, parameters = processed_template
    raise Error, 'Template variables are incomplete' if name.blank? || parameters.nil?

    message_id = @inbox.channel.send_template(
      @phone_number,
      { name: name, namespace: namespace, lang_code: language, parameters: parameters },
      nil
    )
    raise Error, 'Meta did not return a message ID' if message_id.blank?

    message_id
  end

  private

  def validate!
    raise Error, 'WhatsApp Cloud provider required' unless @inbox.channel.provider == 'whatsapp_cloud'
    raise Error, 'Approved WhatsApp template required' unless @template.approved?
    raise Error, 'Template does not belong to this inbox' unless @template.inbox_id == @inbox.id
    raise Error, 'Enter a valid E.164 phone number' unless @phone_number.match?(E164_PHONE_NUMBER)
  end

  def processed_template
    template_params = if @media_blob
                        Whatsapp::CampaignMediaService.new(blob: @media_blob, template: @template).apply(@template_params)
                      else
                        @template_params
                      end

    Whatsapp::TemplateProcessorService.new(
      channel: @inbox.channel,
      template_params: template_params,
      template: @template.processor_payload
    ).call
  rescue Whatsapp::CampaignMediaService::Error => e
    raise Error, e.message
  end
end
