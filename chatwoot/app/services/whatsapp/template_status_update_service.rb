class Whatsapp::TemplateStatusUpdateService
  def initialize(channel:, params:)
    @channel = channel
    @change = params.dig(:entry, 0, :changes, 0) || {}
  end

  def perform
    template = find_template
    return if template.blank?

    attributes = { last_synced_at: Time.current }
    status_event? ? add_status_attributes(attributes) : add_quality_attributes(attributes)
    template.update!(attributes)
  end

  private

  def find_template
    value = @change[:value] || {}
    scope = @channel.inbox.whatsapp_templates
    template_id = value[:message_template_id] || value[:id]
    return scope.find_by(meta_template_id: template_id.to_s) if template_id.present?

    scope.find_by(name: value[:message_template_name], language: value[:message_template_language])
  end

  def status_event?
    @change[:field] == 'message_template_status_update'
  end

  def add_status_attributes(attributes)
    value = @change[:value] || {}
    status = value[:event]&.upcase
    return if status.blank?

    attributes[:status] = status
    attributes[:rejection_reason] = value[:reason] if status == 'REJECTED'
    attributes[:approved_at] = Time.current if status == 'APPROVED'
    attributes[:rejected_at] = Time.current if status == 'REJECTED'
  end

  def add_quality_attributes(attributes)
    value = @change[:value] || {}
    attributes[:quality_rating] = value[:event] || value[:new_quality_score]
  end
end
