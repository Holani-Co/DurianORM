# The one log line per AI request (docs/mcp/MCP.md, section 10), refusals included:
#   ai {"at":"…","ai":"pass","user":"…","user_id":42,"client":"Claude","sub":"…","method":"GET",
#       "tool":"list_conversations","route":"GET /api/v1/accounts/:account_id/conversations","status":200,"ms":84,
#       "ref":"Ab3dEf"}
# with "refused":"<code>" when refused. Written to stdout whatever LOG_LEVEL says (on the box it lands in
# /var/log/chatwoot-web.log); in tests to Rails' log. Never the pass, never the real path or query (they can carry a
# customer's name or number): only the route pattern, and `ref`, the jti's first 6 characters.
module DurianOs::Ai::Log
  ORDER = %w[at ai user user_id client sub ceiling method tool route status ms ref refused error].freeze

  module_function

  def write(fields)
    line = "ai #{JSON.generate(ordered(fields))}"
    return Rails.logger.info(line) if Rails.env.test?

    $stdout.write("#{line}\n")
    $stdout.flush
  end

  def ordered(fields)
    all = { 'at' => Time.now.utc.iso8601, 'ai' => 'pass' }.merge(fields.stringify_keys)
    ORDER.each_with_object({}) { |name, line| line[name] = all[name] if all.key?(name) }
  end
end
