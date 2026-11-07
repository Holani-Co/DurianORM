# Which route an AI request reaches, and whether its caller may use it (docs/mcp/MCP.md, section 6).
#
# Runs in the middleware after the person is found and before Rails dispatches. Rails' own router recognises the
# route on a copy of the request (the middleware is the last in the stack, so it sees the method and path the router
# will); the registry decides by `controller#action`. Refusals are 403:
# - a mounted app or engine, a redirect, no route, or a key the registry does not list: `not_a_tool`;
# - browser-only: `web_only`, with its reason; a write: `read_only`, for every ceiling and every verb;
# - a read reached any other way than the registry names it (another method, path pattern or format, or a route with
#   request constraints, which are never evaluated here): `not_a_tool`;
# - another account than the managed one: `wrong_account`;
# - `who` with the ceiling applied, using ORM's own checks: `admin_only`, `no_report_access`, `no_kb_access`.
# Custom::Concerns::ApplicationControllerConcern looks again at what actually runs (`recheck`), so a mismatch
# between the router and the gate can only refuse.
class DurianOs::Ai::Gate
  Decision = Struct.new(:key, :entry, :route, keyword_init: true) do
    def tool
      entry&.name
    end
  end

  NUMBER = /\A\d+\z/

  attr_reader :route_label, :tool

  def self.recheck(controller, decision)
    entry = decision&.entry
    return 'not_a_tool' unless controller.is_a?(Api::BaseController) && entry&.read?
    return 'not_a_tool' unless "#{controller.controller_path}##{controller.action_name}" == entry.key

    request = controller.request
    return 'not_a_tool' unless request.request_method == entry.http_method && request.format.symbol == :json

    'wrong_account' unless account_ok?(entry, controller.params[entry.account_param])
  end

  def self.account_ok?(entry, value)
    return true if entry.account_param == 'none'

    NUMBER.match?(value.to_s) && value.to_i == DurianOs::Config.account_id
  end

  def initialize(env, caller)
    @env = env
    @caller = caller
  end

  def decide!
    route, params = recognize
    refuse!('not_a_tool') if route.nil?

    @route_label = "#{@env['REQUEST_METHOD']} #{pattern(route)}"
    entry = registry_entry(route)
    @tool = entry.name if entry&.read? || entry&.write?
    check_entry!(entry, route, params)
    check_who!(entry)
    Decision.new(key: entry.key, entry: entry, route: @route_label)
  end

  private

  def recognize
    request = ActionDispatch::Request.new(@env.dup)
    Rails.application.routes.router.recognize(request) { |route, params| return [route, params] }
    nil
  end

  # Only a controller route has a registry entry; a mounted app, engine or redirect is never a tool.
  def registry_entry(route)
    app = route.app
    refuse!('not_a_tool') unless app.respond_to?(:dispatcher?) && app.dispatcher?
    refuse!('not_a_tool') if route.path.names.intersect?(%w[controller action])

    controller, action = route.defaults.values_at(:controller, :action)
    refuse!('not_a_tool') if controller.blank? || action.blank?

    DurianOs::Ai::Registry.entry_for("#{controller}##{action}") || refuse!('not_a_tool')
  end

  def check_entry!(entry, route, params)
    refuse!('web_only', web_only_words(entry.reason)) if entry.web_only?
    refuse!('read_only', DurianOs::Ai::READS_ONLY) if entry.write?

    plain = route.app.is_a?(ActionDispatch::Routing::RouteSet::Dispatcher)
    refuse!('not_a_tool') unless plain && @env['REQUEST_METHOD'] == entry.http_method && pattern(route) == entry.path
    refuse!('not_a_tool') unless params[:format].nil? || params[:format].to_s == 'json'
    refuse!('wrong_account', DurianOs::Ai::WRONG_ACCOUNT) unless self.class.account_ok?(entry, params[entry.account_param.to_sym])
  end

  # `who`, with the ceiling (DurianOs::Current.ai is set): the person's own row in the managed account, read with
  # ORM's own checks, as the browser path would build the Pundit context.
  def check_who!(entry)
    return if entry.who == 'everyone'

    account_user = AccountUser.includes(:account).find_by(account_id: DurianOs::Config.account_id, user_id: @caller.user_id)
    refusal = DurianOs::Ai::Registry.who_refusal(entry.who, @caller.user, account_user&.account, account_user)
    refuse!(*refusal) if refusal
  end

  def pattern(route)
    route.path.spec.to_s.delete_suffix('(.:format)')
  end

  def web_only_words(reason)
    "#{DurianOs::Ai::NOT_FOR_AI} #{DurianOs::Ai::Registry::WEB_ONLY_REASONS.fetch(reason, '')}".strip
  end

  def refuse!(code, message = DurianOs::Ai::NOT_FOR_AI)
    DurianOs::Ai.refuse!(403, code, message)
  end
end
