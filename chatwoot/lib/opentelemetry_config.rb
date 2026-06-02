require 'opentelemetry/sdk'
require 'opentelemetry/exporter/otlp'
require 'base64'

module OpentelemetryConfig
  class << self
    def tracer
      initialize! unless initialized?
      OpenTelemetry.tracer_provider.tracer('chatwoot')
    end

    def initialized?
      @initialized ||= false
    end

    def initialize!
      return if @initialized
      return mark_initialized unless langfuse_provider?
      return mark_initialized unless langfuse_credentials_present?

      configure_opentelemetry
      mark_initialized
    rescue StandardError => e
      Rails.logger.error "Failed to configure OpenTelemetry: #{e.message}"
      mark_initialized
    end

    def reset!
      @initialized = false
    end

    private

    def mark_initialized
      @initialized = true
    end

    # Prefer the ENV value (deployment source of truth) and fall back to the
    # DB-configured value (super-admin UI). ENV takes precedence because
    # ConfigLoader seeds installation_config.yml defaults (e.g. a US
    # LANGFUSE_BASE_URL) into the DB, which would otherwise override the
    # operator's .env and cause host/credential mismatches.
    def config_value(name)
      ENV.fetch(name, nil).presence || InstallationConfig.find_by(name: name)&.value.presence
    end

    def langfuse_provider?
      config_value('OTEL_PROVIDER') == 'langfuse'
    end

    def langfuse_credentials_present?
      endpoint = config_value('LANGFUSE_BASE_URL')
      public_key = config_value('LANGFUSE_PUBLIC_KEY')
      secret_key = config_value('LANGFUSE_SECRET_KEY')

      if endpoint.blank? || public_key.blank? || secret_key.blank?
        Rails.logger.error 'OpenTelemetry disabled (LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY or LANGFUSE_SECRET_KEY is missing)'
        return false
      end

      true
    end

    def langfuse_credentials
      {
        endpoint: config_value('LANGFUSE_BASE_URL'),
        public_key: config_value('LANGFUSE_PUBLIC_KEY'),
        secret_key: config_value('LANGFUSE_SECRET_KEY')
      }
    end

    def traces_endpoint
      credentials = langfuse_credentials
      "#{credentials[:endpoint]}/api/public/otel/v1/traces"
    end

    def exporter_config
      credentials = langfuse_credentials
      auth_header = Base64.strict_encode64("#{credentials[:public_key]}:#{credentials[:secret_key]}")

      config = {
        endpoint: traces_endpoint,
        headers: { 'Authorization' => "Basic #{auth_header}" }
      }

      config[:ssl_verify_mode] = OpenSSL::SSL::VERIFY_NONE if Rails.env.development?
      config
    end

    def configure_opentelemetry
      OpenTelemetry::SDK.configure do |c|
        c.service_name = 'chatwoot'
        exporter = OpenTelemetry::Exporter::OTLP::Exporter.new(**exporter_config)
        c.add_span_processor(OpenTelemetry::SDK::Trace::Export::BatchSpanProcessor.new(exporter))
        Rails.logger.info 'OpenTelemetry initialized and configured to export to Langfuse'
      end
    end
  end
end
