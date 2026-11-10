# AI connections to ORM through Durian OS's AI front door (docs/mcp/MCP.md).
#
# An AI reaches ORM with `Authorization: Bearer <pass>`, a one-use EdDSA JWT Durian OS mints for one call. The
# middleware (custom/lib/durian_os_ai_door.rb, logic in DurianOs::Ai::Door) checks the pass, finds the person,
# applies the gate and runs the request as that person through the browser's own controllers, policies and finders.
# Only a Full connection (pass max_level administer) may change anything, through the offered write tools.
module DurianOs::Ai
  # The env keys the door sets for the controller hook (Custom::Concerns::ApplicationControllerConcern).
  ENV_KEY = 'durian_os.ai'.freeze
  DECISION_KEY = 'durian_os.ai.decision'.freeze
  REFUSED_KEY = 'durian_os.ai.refused'.freeze

  PASS_INVALID = 'This Durian OS connection is not valid. Reconnect in Durian OS.'.freeze
  NOT_FOR_AI = "This isn't available to AI connections.".freeze
  READ_ONLY_PASS = 'This connection is read-only, so it cannot change anything. Reconnect with Full access in Durian OS ' \
                   'to make changes.'.freeze
  BAD_INPUT = 'This change was not sent in the form its tool takes (JSON body, only the tool\'s own fields, no query).'.freeze
  ADMIN_ONLY_WRITE = 'Only an ORM administrator can change this, on a Durian OS connection with full access.'.freeze
  NOT_FOUND = 'ORM could not find that, or the person cannot see it.'.freeze
  WRONG_ACCOUNT = "ORM's AI connection works in the Durian account only.".freeze
  ADMIN_ONLY = 'Only an ORM administrator can read this, on a Durian OS connection with full access.'.freeze
  NO_REPORT_ACCESS = 'This needs report access in ORM.'.freeze
  NO_KB_ACCESS = 'This needs help center access in ORM.'.freeze
  ROLE_REFUSED = 'Your ORM role does not allow this.'.freeze
  BUSY = 'ORM is answering other AI requests. Try again in a moment.'.freeze
  WOULD_WRITE = 'This read would change data in ORM, so an AI connection cannot run it yet.'.freeze
  NOT_JSON = 'ORM answered this with a file, not data, so an AI connection cannot read it. Open it in ORM.'.freeze
  UNAVAILABLE = 'ORM cannot check AI connections right now. Try again in a moment.'.freeze

  # An AI request refused: the HTTP status, the code for the log line and the words the AI gets.
  class Refused < StandardError
    attr_reader :status, :code, :headers

    def initialize(status, code, message, headers: {})
      @status = status
      @code = code
      @headers = headers
      super(message)
    end
  end

  module_function

  def refuse!(status, code, message, headers: {})
    raise Refused.new(status, code, message, headers: headers)
  end

  # An audit row written during an AI request names the AI in its comment.
  def mark_audit(audit)
    caller = DurianOs::Current.ai
    return if caller.nil?

    audit.comment = ["via #{caller.client} (AI connection)", audit.comment.presence].compact.join(' · ').first(255)
  end
end
