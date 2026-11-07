require 'rails_helper'

# ORM's AI tool registry is complete (docs/mcp/MCP.md, section 8): every route Rails mounts is listed exactly once in
# custom/config/mcp_registry.yml, as a read tool, a write tool or browser-only. When this fails on a new route, list it
# in the same change; after changing a read, run `bundle exec rake durian_os:mcp_tools` and name the tool on the page.
RSpec.describe 'ORM AI tool registry' do
  let(:registry) { DurianOs::Ai::Registry }
  let(:raw) { Psych.safe_load(Rails.root.join(DurianOs::Ai::Registry::PATH).read, permitted_classes: [], aliases: false) }
  let(:listed) do
    raw['read'].pluck('action') + raw['write'].pluck('action') + raw['web_only'].values.flatten
  end
  let(:mounted) { Rails.application.routes.routes.reject(&:internal).group_by { |route| key_for(route) } }
  let(:tools) { registry.reads + registry.writes }

  # Never a tool, read or write: these change access or sign-in, or hold tokens and secrets.
  let(:never_keys) do
    %w[
      api/v1/accounts/agents#create api/v1/accounts/agents#update api/v1/accounts/agents#destroy
      api/v1/accounts/agents#bulk_create
      api/v1/accounts/inbox_members#create api/v1/accounts/inbox_members#update api/v1/accounts/inbox_members#destroy
      api/v1/accounts/team_members#create api/v1/accounts/team_members#update api/v1/accounts/team_members#destroy
      api/v1/accounts/custom_roles#create api/v1/accounts/custom_roles#update api/v1/accounts/custom_roles#destroy
      api/v1/profiles#show api/v1/profiles#update api/v1/profiles#reset_access_token
      api/v1/accounts/inboxes#reset_secret api/v1/accounts/inboxes#set_agent_bot api/v1/accounts/inboxes#agent_bot
      api/v1/accounts/integrations/hooks#show api/v1/accounts/integrations/hooks#create
      api/v1/accounts/integrations/hooks#update api/v1/accounts/integrations/hooks#destroy
    ]
  end
  let(:never_prefixes) do
    %w[
      api/v1/accounts/agent_bots# api/v1/accounts/saml_settings# api/v1/profile/mfa# api/v1/notification_subscriptions#
      api/v1/accounts/webhooks# api/v1/accounts/integrations/apps# api/v1/accounts/captain/custom_tools#
      platform/ super_admin/ devise api/v1/widget/ public/ webhooks/
    ]
  end

  def key_for(route)
    app = route.app
    return "#{route.defaults[:controller]}##{route.defaults[:action]}" if app.respond_to?(:dispatcher?) && app.dispatcher?
    return "#{route.verb} #{pattern(route)}" if app.respond_to?(:app) && app.app.is_a?(ActionDispatch::Routing::Redirect)

    "mount #{route.path.spec}"
  end

  def pattern(route)
    route.path.spec.to_s.delete_suffix('(.:format)')
  end

  it 'lists every mounted route exactly once' do
    missing = mounted.keys - listed
    expect(missing).to be_empty,
                       "Mounted but not in #{DurianOs::Ai::Registry::PATH}; list each as a read, a write or web_only: " \
                       "#{missing.map { |key| "#{key} (#{mounted[key].map { |route| "#{route.verb} #{pattern(route)}" }.join(', ')})" }.join('; ')}"
    doubled = listed.tally.select { |_, count| count > 1 }.keys
    expect(doubled).to be_empty, "Listed more than once: #{doubled.join(', ')}"
    gone = listed - mounted.keys
    expect(gone).to be_empty, "Listed but no longer mounted; remove: #{gone.join(', ')}"
  end

  it 'offers each read on a mounted route of its action, as GET or as a POST with a reason' do
    registry.reads.each do |entry|
      route = mounted.fetch(entry.key, []).find do |candidate|
        candidate.verb.split('|').include?(entry.http_method) && pattern(candidate) == entry.path
      end
      expect(route).to be_present, "#{entry.name}: #{entry.route} is not a mounted route of #{entry.key}"
      expect(route.app).to be_a(ActionDispatch::Routing::RouteSet::Dispatcher), "#{entry.name}: a read has no request constraints"
      expect(%w[GET POST]).to include(entry.http_method)
      expect(entry.data['post']).to be_present, "#{entry.name}: a POST read needs a post: reason" if entry.http_method == 'POST'
    end
  end

  it 'finds the managed account and every path id of a read' do
    registry.reads.each do |entry|
      params = entry.path.scan(/:(\w+)/).flatten
      if entry.account_param == 'none'
        expect(entry.key).to eq('api/v1/ai#me')
      else
        expect(params).to include(entry.account_param), "#{entry.name}: no :#{entry.account_param} in #{entry.path}"
      end
      path_args = Array(entry.data['args']).select { |arg| arg['in'] == 'path' }
      expect(path_args.map { |arg| arg['param'] || arg['name'] }).to match_array(params - [entry.account_param]), entry.name
      expect(path_args.all? { |arg| arg['required'] == true }).to be(true), "#{entry.name}: path args are required"
      Array(entry.data['args']).each do |arg|
        expect(%w[path query body]).to include(arg['in']), "#{entry.name}: #{arg['name']}"
        expect(%w[string integer number boolean array object]).to include(arg['type']), "#{entry.name}: #{arg['name']}"
      end
    end
  end

  it 'names every tool once, the way Durian OS shows it (orm__<name>, at most 64 characters)' do
    names = tools.map(&:name)
    expect(names.tally.select { |_, count| count > 1 }.keys).to be_empty
    names.each do |name|
      expect(name).to match(/\A[a-z][a-z0-9_]*\z/)
      expect(name.length).to be <= 59, name
    end
  end

  it 'gives each read a short title and description, and says who it is for' do
    registry.reads.each do |entry|
      title = entry.data['title'].to_s
      description = entry.data['description'].to_s
      expect(title.split.size).to be_between(1, 7), entry.name
      expect(title).not_to end_with('.'), entry.name
      expect(description.length).to be_between(1, 300), entry.name
      expect(description).to end_with('Administrators only.'), entry.name if entry.who == 'admin'
      expect(description).to end_with('Needs report access.'), entry.name if entry.who == 'reports'
    end
  end

  it 'never offers access, sign-in, tokens or secrets, read or write' do
    keys = tools.map(&:key)
    expect(keys & never_keys).to be_empty
    never_prefixes.each { |prefix| expect(keys.grep(/\A#{Regexp.escape(prefix)}/)).to be_empty, prefix }
    (never_keys & mounted.keys).each { |key| expect(registry.entry_for(key)).to be_web_only, key }
  end

  it 'gates tools only on features that exist' do
    features = YAML.safe_load(Rails.root.join('config/features.yml').read).pluck('name')
    registry.reads.filter_map(&:feature).uniq.each { |feature| expect(features).to include(feature) }
  end

  it 'keeps its counts' do
    expect([registry.reads.size, registry.writes.size, registry.web_only.size]).to eq([163, 190, 414])
    expect(registry.reads.count { |entry| entry.http_method == 'POST' }).to eq(11)
  end

  it 'matches mcp_server/tools.json, which rake durian_os:mcp_tools writes' do
    path = Rails.root.join('../mcp_server/tools.json')
    expect(path).to exist
    expect(JSON.parse(path.read)).to eq(JSON.parse(registry.export_json)), 'Run bundle exec rake durian_os:mcp_tools'
  end

  it 'is named on the design page, every read and write tool' do
    page = Rails.root.join('../docs/mcp/MCP.md').read
    tools.each { |entry| expect(page).to include("`#{entry.name}`"), "#{entry.name} is not on docs/mcp/MCP.md" }
  end

  it 'puts the AI middleware last' do
    expect(Rails.application.middleware.last.klass).to eq(DurianOsAiDoor)
  end
end
