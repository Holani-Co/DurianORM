# ORM's AI tool registry (docs/mcp/MCP.md, section 8): custom/config/mcp_registry.yml lists every route Rails
# mounts exactly once, by the key Rails dispatches it with: a read tool (offered), a write tool (offered to a Full
# connection, or listed and refused with a reason) or browser-only with a reason. Loaded once with Psych safe_load,
# frozen and indexed by key. Loaded lazily: a broken file refuses AI requests (503) and never stops the browser.
module DurianOs::Ai::Registry
  PATH = 'custom/config/mcp_registry.yml'.freeze
  WHO = %w[everyone admin reports kb].freeze
  METHODS = %w[GET POST].freeze
  WRITE_METHODS = %w[POST PATCH DELETE].freeze
  ARG_KEYS = %w[name in param type items required enum minimum maxLength maxItems pattern description].freeze
  GUARDS = %w[conversation_attributes contact_attributes labels_known macro_safe].freeze

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

  # Why a write is listed and refused (section 11.3): `refused: <key>` in the registry, and the words the AI gets.
  WRITE_REFUSED_REASONS = {
    'customer' => 'It reaches a customer (a reply, a transcript, an offer, a campaign or another send on WhatsApp, ' \
                  'Instagram, email or reviews), so it stays in ORM itself for now.',
    'outside' => 'It calls a paid AI model or an outside system (Zoho, Meta, Linear, Google or email), so it stays in ' \
                 'ORM itself for now.',
    'bulk' => 'It changes or deletes many records at once, so it stays in ORM itself.',
    'deletion' => 'Deleting conversations, contacts, companies and messages cannot be undone, so it stays in ORM itself.',
    'settings' => 'Settings and workflows (account, inboxes, teams, policies, automation, campaigns, Captain and the ' \
                  'help center) stay in ORM itself for now.',
    'later' => 'An AI connection cannot do this yet: it is not in the first set of changes it can make. Do it in ORM.'
  }.freeze

  class Broken < StandardError; end

  Entry = Struct.new(:kind, :key, :name, :http_method, :path, :who, :feature, :account_param, :reason, :data,
                     keyword_init: true) do
    def read?
      kind == :read
    end

    # Every write route; `offered_write?` the ones an AI connection with Full access may call, `refused_write?` the
    # ones that are listed and always refused (`reason` says why).
    def write?
      kind == :write
    end

    def offered_write?
      write? && reason.nil?
    end

    def refused_write?
      write? && !reason.nil?
    end

    # A tool the AI may call: a read, or an offered write.
    def tool?
      read? || offered_write?
    end

    def web_only?
      kind == :web_only
    end

    def route
      "#{http_method} #{path}" if http_method
    end

    def destructive?
      data['destructive'] == true
    end

    def guard
      data['guard']
    end

    # Body values the registry pins (a private note is `private: true`), and what a mismatch is told.
    def fixed
      data['fixed'] || {}
    end

    def fixed_refusal
      data['fixed_refusal']
    end

    def args
      Array(data['args'])
    end

    # The body keys an offered write may carry: its body arguments and its pinned values.
    def body_keys
      args.select { |arg| arg['in'] == 'body' }.pluck('name') | fixed.keys
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

  def offered_writes
    writes.select(&:offered_write?)
  end

  def refused_writes
    writes.select(&:refused_write?)
  end

  def web_only
    index[:web_only]
  end

  def data
    index[:data]
  end

  # The tools a member may call: the reads, and with `writes: true` (a Full connection) the offered writes, whose
  # `who` passes with ORM's own checks for their row (with the AI ceiling applied when DurianOs::Current.ai is set)
  # and whose `feature`, if any, is on for the account. Sorted by name.
  def tools_for(user, account, account_user, writes: false)
    entries = writes ? reads + offered_writes : reads
    entries.select { |entry| who_allows?(entry.who, user, account, account_user) && feature_on?(entry.feature, account) }
           .map(&:name).sort
  end

  # Nil when `who` allows the member, else [code, words] for the gate's refusal.
  def who_refusal(who, user, account, account_user, write: false)
    return if who_allows?(who, user, account, account_user)

    case who
    when 'admin' then ['admin_only', write ? DurianOs::Ai::ADMIN_ONLY_WRITE : DurianOs::Ai::ADMIN_ONLY]
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

  # What `rake durian_os:mcp_tools` writes to ../mcp_server/tools.json: the reads and the offered writes, and the counts.
  def export
    {
      'version' => data['version'],
      'source' => "chatwoot/#{PATH}",
      'counts' => { 'read' => reads.size, 'write_offered' => offered_writes.size, 'write_refused' => refused_writes.size,
                    'web_only' => web_only.size },
      'tools' => (reads + offered_writes).map { |entry| export_tool(entry) }
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
    }.merge(entry.write? ? export_write(entry) : {})
  end

  # What only a write carries: the server labels it not read-only, and destructive where it cannot be undone.
  def export_write(entry)
    raw = entry.data
    { 'write' => true, 'destructive' => entry.destructive?, 'idempotent' => raw['idempotent'] == true,
      'open_world' => raw['open_world'] == true, 'fixed' => entry.fixed }
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

  # An offered write is a full entry; a refused one has `refused: <reason>`.
  def write_entry(item)
    entry = tool_entry(:write, item)
    return refused_entry(entry, item['refused']) if item['refused']

    check_offered!(entry, item)
    entry
  end

  def refused_entry(entry, reason)
    raise Broken, "write #{entry.name}: unknown reason #{reason.inspect}" unless WRITE_REFUSED_REASONS.key?(reason)

    entry.reason = reason
    entry
  end

  def check_offered!(entry, item)
    raise Broken, "write #{entry.name}: who #{entry.who.inspect}" unless WHO.include?(entry.who)
    raise Broken, "write #{entry.name}: a write is POST, PATCH or DELETE" unless WRITE_METHODS.include?(entry.http_method)
    raise Broken, "write #{entry.name}: title and description" unless item['title'].present? && item['description'].present?
    raise Broken, "write #{entry.name}: guard #{item['guard'].inspect}" if item['guard'] && GUARDS.exclude?(item['guard'])
    raise Broken, "write #{entry.name}: fixed needs fixed_refusal" if item['fixed'] && item['fixed_refusal'].blank?
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
