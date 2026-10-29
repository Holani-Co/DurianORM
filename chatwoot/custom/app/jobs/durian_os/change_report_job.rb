# Posts one change report to Durian OS. The body (and its reportId) stays the same on
# every attempt; the timestamp and signature are fresh each time (3.6, 6.3).
# Retries after 1, 5 and 15 minutes, then hourly for 24 hours.
class DurianOs::ChangeReportJob < ApplicationJob
  queue_as :default

  RETRY_WAITS = ([1.minute, 5.minutes, 15.minutes] + Array.new(24, 1.hour)).freeze
  TIMEOUT = 5

  class DeliveryFailed < StandardError; end

  retry_on DeliveryFailed, Net::OpenTimeout, Net::ReadTimeout, SocketError, SystemCallError, OpenSSL::SSL::SSLError,
           wait: ->(executions) { RETRY_WAITS[executions - 1] || 1.hour },
           attempts: RETRY_WAITS.size + 1

  def perform(body)
    return unless DurianOs::Config.reporter_enabled?

    status = deliver(body)
    raise DeliveryFailed, "Durian OS answered #{status}" unless status.between?(200, 299)
  end

  private

  def deliver(body)
    uri = URI.parse(DurianOs::Config.report_url)
    timestamp = Time.now.to_i
    request = Net::HTTP::Post.new(uri.request_uri, DurianOs::Reporter.headers(body, timestamp))
    request.body = body
    http = Net::HTTP.new(uri.host, uri.port)
    http.use_ssl = uri.scheme == 'https'
    http.open_timeout = TIMEOUT
    http.read_timeout = TIMEOUT
    http.write_timeout = TIMEOUT
    http.request(request).code.to_i
  end
end
