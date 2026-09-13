# Parses a Meta Cloud API send response into a structured result the campaign
# delivery job can act on, preserving the real error code/message (unlike the
# Message-based path, which discards them). Returns one of:
#   { message_id: 'wamid...' }                       on success
#   { error_code: '130429', error_message: '...' }   on failure
class Whatsapp::SendResult
  def self.from_response(response)
    parsed = response.parsed_response
    if response.success? && parsed.is_a?(Hash) && parsed['error'].blank?
      { message_id: parsed.dig('messages', 0, 'id') }
    else
      Rails.logger.error response.body
      error = parsed.is_a?(Hash) ? parsed['error'] : nil
      { error_code: error&.dig('code')&.to_s, error_message: extract_message(error) }
    end
  end

  def self.extract_message(error)
    return 'Unknown error from Meta' if error.blank?

    error.dig('error_data', 'details').presence || error['message'].presence || 'Unknown error from Meta'
  end
end
