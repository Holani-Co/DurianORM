# One AI request through ORM (docs/mcp/MCP.md, sections 1 to 10), called by the DurianOsAiDoor middleware for a
# request whose Authorization is `Bearer <JWT>`:
# 1. every other credential is removed from the request, so nothing but the pass can sign it in;
# 2. the pass (DurianOs::Ai::Pass), a slot (429, the pass not spent), the jti spent once, the person
#    (DurianOs::Ai::Caller);
# 3. the gate (DurianOs::Ai::Gate), with DurianOs::Current.ai set so the ceiling applies;
# 4. the request runs as the person through the browser's controllers, inside while_preventing_writes: only an offered
#    write from a pass with Full access (`administer`) that the gate let through runs without it;
# 5. the answer is cleaned: a 401 after the pass was accepted is a role refusal (403), secrets and signed file links
#    are removed, auth headers dropped, and a read that answers anything but JSON is 502;
# 6. the slot is given back and one log line is written (DurianOs::Ai::Log), refusals included, with the write's
#    outcome (`write`: done, failed or refused) for a write tool.
class DurianOs::Ai::Door
  # Session and token credentials a browser or an integration could send (Rack env names), and the bare
  # `api_access_token` key that Api::BaseController's `request.headers[:api_access_token]` also reads.
  CREDENTIALS = %w[
    HTTP_COOKIE HTTP_API_ACCESS_TOKEN api_access_token HTTP_ACCESS_TOKEN HTTP_CLIENT HTTP_UID HTTP_EXPIRY HTTP_TOKEN_TYPE
  ].freeze
  # Cookies Rack or Rails may already have parsed and cached in the env.
  PARSED_COOKIES = %w[rack.request.cookie_hash rack.request.cookie_string action_dispatch.cookies].freeze
  BEARER = /\A\s*Bearer +([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)\s*\z/i
  JSON_TYPE = %r{\Aapplication/(?:[a-z0-9.+-]+\+)?json\b}i

  def initialize(app, env)
    @app = app
    @env = env
    @started = Process.clock_gettime(Process::CLOCK_MONOTONIC)
    @line = { 'method' => env['REQUEST_METHOD'] }
  end

  def call
    strip_credentials
    claims = DurianOs::Ai::Pass.verify(bearer)
    @line.merge!('sub' => claims['sub'], 'ref' => claims['jti'].first(6),
                 'client' => DurianOs::Ai::Caller.client_of(claims['client']))
    slots = DurianOs::Ai::Slots.take!(claims['sub'])
    begin
      DurianOs::Ai::Slots.spend!(claims['jti'], claims['sub'])
      caller = DurianOs::Ai::Caller.find!(claims)
      @line.merge!('user' => caller.user.email, 'user_id' => caller.user_id, 'ceiling' => caller.ceiling)
      DurianOs::Current.set(ai: caller) { serve(caller) }
    ensure
      slots.release
    end
  rescue DurianOs::Ai::Refused => e
    refused(e)
  rescue DurianOs::Ai::Registry::Broken => e
    Rails.logger.error("[DurianOS] AI registry broken: #{e.message}")
    refused(DurianOs::Ai::Refused.new(503, 'registry', DurianOs::Ai::UNAVAILABLE))
  rescue StandardError => e
    @line['status'] = 500
    @line['error'] = e.class.name
    raise
  ensure
    write_line
  end

  private

  def strip_credentials
    CREDENTIALS.each { |name| @env.delete(name) }
    PARSED_COOKIES.each { |name| @env.delete(name) }
  end

  # The token of exactly one `Bearer <JWT>`, else nil (DurianOs::Ai::Pass refuses it as `malformed`, after the
  # switch): two Authorization headers arrive joined by a comma.
  def bearer
    BEARER.match(@env['HTTP_AUTHORIZATION'].to_s)&.[](1)
  end

  def serve(caller)
    gate = DurianOs::Ai::Gate.new(@env, caller)
    begin
      decision = gate.decide!
    ensure
      @line.merge!('tool' => gate.tool, 'route' => gate.route_label)
      @write = gate.write
    end

    @env[DurianOs::Ai::ENV_KEY] = caller
    @env[DurianOs::Ai::DECISION_KEY] = decision
    status, headers, body = run(decision)
    answer(status, headers, body)
  end

  # Reads, and every write the gate did not let through, cannot write: any INSERT, UPDATE or DELETE raises.
  def run(decision)
    return @app.call(@env) if decision.entry.offered_write? && @env[DurianOs::Ai::ENV_KEY].full_access?

    ActiveRecord::Base.while_preventing_writes { @app.call(@env) }
  rescue ActiveRecord::ReadOnlyError
    DurianOs::Ai.refuse!(403, 'would_write', DurianOs::Ai::WOULD_WRITE)
  end

  def answer(status, headers, body)
    text = read(body)
    headers = DurianOs::Ai::Clean.headers(headers)
    # Refused by the controller hook (the second look, or a read that tried to write): its code goes to the log.
    @line['refused'] = @env[DurianOs::Ai::REFUSED_KEY] if @env[DurianOs::Ai::REFUSED_KEY]
    return role_refusal(text) if status.to_i == 401
    return finish(status, headers, '') if text.empty?
    return refused(DurianOs::Ai::Refused.new(502, 'not_json', DurianOs::Ai::NOT_JSON)) unless json?(headers)

    finish(status, headers, JSON.generate(DurianOs::Ai::Clean.json(JSON.parse(text))))
  rescue JSON::ParserError
    refused(DurianOs::Ai::Refused.new(502, 'not_json', DurianOs::Ai::NOT_JSON))
  end

  # Chatwoot answers a Pundit refusal and "not authorized to access this account" with 401. After the pass was
  # accepted that is the person's role, never a reason to reconnect: 403, in ORM's own words.
  def role_refusal(text)
    words = begin
      parsed = JSON.parse(text)
      parsed.is_a?(Hash) ? (parsed['error'].presence || parsed['message'].presence || Array(parsed['errors']).first) : nil
    rescue JSON::ParserError
      nil
    end
    words = DurianOs::Ai::ROLE_REFUSED unless words.is_a?(String) && words.present?
    refused(DurianOs::Ai::Refused.new(403, 'role', words))
  end

  def read(body)
    text = +''
    body.each { |chunk| text << chunk.to_s }
    text
  ensure
    body.close if body.respond_to?(:close)
  end

  def json?(headers)
    JSON_TYPE.match?(DurianOs::Ai::Clean.header(headers, 'content-type').to_s)
  end

  def finish(status, headers, text)
    @line['status'] = status.to_i
    headers = headers.reject { |name, _| name.to_s.casecmp?('content-length') }
    headers['content-length'] = text.bytesize.to_s unless text.empty? && [204, 304].include?(status.to_i)
    [status, headers, [text]]
  end

  # What became of a write tool's request: `done` (2xx), `refused` (by the gate, the hook or a policy) or `failed`.
  def write_outcome
    return 'refused' if @line['refused']

    @line['status'].to_i.between?(200, 299) ? 'done' : 'failed'
  end

  def refused(error)
    @line['status'] = error.status
    @line['refused'] = error.code
    headers = { 'content-type' => 'application/json; charset=utf-8', 'cache-control' => 'no-store' }.merge(error.headers)
    body = JSON.generate(error: error.message)
    [error.status, headers.merge('content-length' => body.bytesize.to_s), [body]]
  end

  def write_line
    @line['ms'] = ((Process.clock_gettime(Process::CLOCK_MONOTONIC) - @started) * 1000).round
    @line['write'] = write_outcome if @write
    DurianOs::Ai::Log.write(@line)
  rescue StandardError
    nil
  end
end
