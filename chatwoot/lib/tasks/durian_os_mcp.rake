# Durian OS AI connections (docs/mcp/MCP.md, section 8): ORM's MCP server (../mcp_server, Python) reads its tools from
# mcp_server/tools.json, never Ruby, YAML or Rails' environment. Run this after changing a read in
# custom/config/mcp_registry.yml; spec/custom/config/mcp_registry_spec.rb fails while the file is out of date.
namespace :durian_os do
  desc "Write ../mcp_server/tools.json: ORM's AI read tools from custom/config/mcp_registry.yml"
  task mcp_tools: :environment do
    path = Rails.root.join('../mcp_server/tools.json')
    FileUtils.mkdir_p(path.dirname)
    File.write(path, DurianOs::Ai::Registry.export_json)
    counts = DurianOs::Ai::Registry.export['counts']
    puts "#{path.cleanpath}: #{counts['read']} read tools (#{counts['write']} writes and #{counts['web_only']} browser-only listed)"
  end
end
