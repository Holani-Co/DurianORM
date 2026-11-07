# ORM's AI tool registry (docs/mcp/MCP.md, section 8): custom/config/mcp_registry.yml lists every route Rails
# mounts exactly once, by the key Rails dispatches it with: a read tool (offered), a write tool (listed, refused in
# v1) or browser-only with a reason. Loaded once with Psych safe_load, frozen and indexed by key. Loaded lazily: a
# broken file refuses AI requests (503) and never stops the browser.
module DurianOs::Ai::Registry
  PATH = 'custom/config/mcp_registry.yml'.freeze
  WHO = %w[everyone admin reports kb].freeze
  METHODS = %w[GET POST].freeze
  ARG_KEYS = %w[name in param type items required enum minimum maxLength pattern description].freeze

  # The words the gate adds for each browser-only reason (section 11.4).
  WEB_ONLY_REASONS = {
    'sign_in' => 'Signing in and out, passwords, email confirmation, two-factor codes and single sign-on stay in the ' \
                 'browser. An AI connection signs in through Durian OS.',
    'access' => 'It changes who can use ORM or what they can see. That is done by a person in Durian OS or ' \
                "ORM's settings, never through an AI connection.",
    'tokens' => 'It shows or resets an access token or secret. Tokens never reach an AI connection.',
    'secrets' => 'These settings hold credentials or signing secrets, so they are read and changed in the browser only.',
    'channels' => 'Connecting a channel with its provider happens in the browser.',
    'files' => 'It takes a file from the browser, removes an avatar or logo, or serves a stored file.',
    'bytes' => 'It gives a file to download. The same numbers are offered as data where ORM has them.',
    'voice' => 'Live voice and WhatsApp calls are taken in the browser.',
    'browser_state' => "This is the browser's own bookkeeping. Reading through an AI never marks anything read.",
    'duplicate' => 'It is a deprecated copy of another read tool.',
    'machine' => "It is called by ORM's own services, not by a person.",
    'webhooks' => 'Inbound webhooks and mail are machine calls signed by other services.',
    'platform' => "The Platform API is for Durian OS's own connector.",
    'super_admin' => "The installation's super admin console stays in the browser.",
    'public' => 'It is customer-facing: the website widget, the public inbox API, surveys and the public help center.',
    'billing' => 'Billing and subscriptions stay in the browser.',
    'pages' => 'It is a web page for the browser.',
    'none' => 'Nothing answers this route.'
  }.freeze

  class Broken < StandardError; end

  Entry = Struct.new(:kind, :key, :name, :http_method, :path, :who, :feature, :account_param, :reason, :data,
                     keyword_init: true) do
    def read?
      kind == :read
    end

    def write?
      kind == :write
    end

    def web_only?
      kind == :web_only
    end

    def route
      "#{http_method} #{path}" if http_method
    end
  end

  LOCK = Mutex.new

  module_function

  # The entry for a key (`controller#action`, `mount /path` or `VERB /path`), or nil when it is not listed.
  def entry_for(key)
    index[:by_key][key]
  end

  def reads
    index[:reads]
  end

  def writes
    index[:writes]
  end

  def web_only
    index[:web_only]
  end

  def data
    index[:data]
  end

  # The read tools a member may call: `who` passed with ORM's own checks for their row (with the AI ceiling applied
  # when DurianOs::Current.ai is set) and `feature`, if any, on for the account. Sorted by name.
  def tools_for(user, account, account_user)
    reads.select { |entry| who_allows?(entry.who, user, account, account_user) && feature_on?(entry.feature, account) }
         .map(&:name).sort
  end

  # Nil when `who` allows the member, else [code, words] for the gate's refusal.
  def who_refusal(who, user, account, account_user)
    return if who_allows?(who, user, account, account_user)

    case who
    when 'admin' then ['admin_only', DurianOs::Ai::ADMIN_ONLY]
    when 'reports' then ['no_report_access', DurianOs::Ai::NO_REPORT_ACCESS]
    when 'kb' then ['no_kb_access', DurianOs::Ai::NO_KB_ACCESS]
    else ['not_a_tool', DurianOs::Ai::NOT_FOR_AI]
    end
  end

  def who_allows?(who, user, account, account_user)
    return false if account_user.nil?

    context = { user: user, account: account, account_user: account_user }
    case who
    when 'everyone' then true
    when 'admin' then account_user.administrator?
    when 'reports' then ReportPolicy.new(context, :report).view?
    when 'kb' then ArticlePolicy.new(context, Article).show?
    else false
    end
  end

  def feature_on?(feature, account)
    feature.nil? || account.feature_enabled?(feature)
  end

  # What `rake durian_os:mcp_tools` writes to ../mcp_server/tools.json: the reads only, and the counts.
  def export
    {
      'version' => data['version'],
      'source' => "chatwoot/#{PATH}",
      'counts' => { 'read' => reads.size, 'write' => writes.size, 'web_only' => web_only.size },
      'tools' => reads.map { |entry| export_tool(entry) }
    }
  end

  def export_json
    "#{JSON.pretty_generate(export)}\n"
  end

  def export_tool(entry)
    raw = entry.data
    {
      'name' => entry.name, 'title' => raw['title'], 'description' => raw['description'],
      'method' => entry.http_method, 'path' => entry.path, 'who' => entry.who, 'feature' => entry.feature,
      'account_param' => entry.account_param,
      'args' => Array(raw['args']).map { |arg| arg.slice(*ARG_KEYS) }
    }
  end

  def reset!
    LOCK.synchronize { @index = nil }
  end

  def index
    @index || LOCK.synchronize { @index ||= build(load_file) }
  end

  def load_file
    Psych.safe_load(File.read(Rails.root.join(PATH)), permitted_classes: [], aliases: false)
  rescue Psych::Exception, SystemCallError => e
    raise Broken, "#{PATH}: #{e.class}"
  end

  def build(raw)
    raise Broken, "#{PATH}: version 1 expected" unless raw.is_a?(Hash) && raw['version'] == 1

    by_key = {}
    reads = Array(raw['read']).map { |item| add(by_key, read_entry(item)) }
    writes = Array(raw['write']).map { |item| add(by_key, write_entry(item)) }
    web_only = web_only_entries(raw['web_only']).map { |entry| add(by_key, entry) }
    { data: deep_freeze(raw), by_key: by_key.freeze, reads: reads.freeze, writes: writes.freeze, web_only: web_only.freeze }
      .freeze
  end

  def read_entry(item)
    entry = tool_entry(:read, item)
    raise Broken, "read #{entry.name}: who #{entry.who.inspect}" unless WHO.include?(entry.who)
    raise Broken, "read #{entry.name}: a read is GET, or POST with a post: reason" unless read_method?(entry, item)

    entry
  end

  def read_method?(entry, item)
    entry.http_method == 'GET' || (entry.http_method == 'POST' && item['post'].is_a?(String) && item['post'].present?)
  end

  def write_entry(item)
    tool_entry(:write, item)
  end

  def tool_entry(kind, item)
    raise Broken, "#{kind}: #{item.inspect}" unless item.is_a?(Hash) && item['name'].is_a?(String) && item['action'].is_a?(String)

    http_method, path = item['route'].to_s.split(' ', 2)
    raise Broken, "#{kind} #{item['name']}: route #{item['route'].inspect}" if path.blank?

    Entry.new(kind: kind, key: item['action'], name: item['name'], http_method: http_method, path: path,
              who: item['who'], feature: item['feature'], account_param: item.fetch('account_param', 'account_id'),
              data: item)
  end

  def web_only_entries(raw)
    raise Broken, 'web_only: reason => keys expected' unless raw.is_a?(Hash)

    raw.flat_map do |reason, keys|
      raise Broken, "web_only: unknown reason #{reason}" unless WEB_ONLY_REASONS.key?(reason)

      Array(keys).map { |key| Entry.new(kind: :web_only, key: key.to_s, reason: reason) }
    end
  end

  def add(by_key, entry)
    raise Broken, "#{entry.key} is listed twice" if by_key.key?(entry.key)

    by_key[entry.key] = entry
  end

  def deep_freeze(value)
    case value
    when Hash then value.each_value { |item| deep_freeze(item) }
    when Array then value.each { |item| deep_freeze(item) }
    end
    value.freeze
  end
end
