# frozen_string_literal: true

# Durian OS AI connections (docs/mcp/MCP.md, section 1), the last middleware in Rails' stack
# (custom/config/initializers/durian_os_ai.rb). A request whose Authorization is `Bearer <credential>` with a `.` in
# the credential (a JWT's shape) is an AI request and goes to DurianOs::Ai::Door, which is reloadable. Every other
# request passes straight through after this one header check: the browser's devise_token_auth Bearer is base64
# JSON, which never holds a `.`, and integrations send api_access_token.
class DurianOsAiDoor
  AI_BEARER = /(?:\A|,)\s*bearer\s+[^,\s]*\./i

  def self.ai_request?(env)
    value = env['HTTP_AUTHORIZATION']
    value.is_a?(String) && AI_BEARER.match?(value)
  end

  def initialize(app)
    @app = app
  end

  def call(env)
    return @app.call(env) unless self.class.ai_request?(env)

    DurianOs::Ai::Door.new(@app, env).call
  end
end
