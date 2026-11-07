# Durian OS AI connections (docs/mcp/MCP.md): the DurianOsAiDoor middleware, used here so it is the last in Rails'
# stack (custom initializers load after config/initializers). It is a thin class outside the autoload paths that
# calls the reloadable DurianOs::Ai::Door; it acts only on `Authorization: Bearer <JWT>` and only while ORM_OS_PASS
# is on (off, every pass is 401).
require Rails.root.join('custom/lib/durian_os_ai_door')

Rails.application.config.middleware.use DurianOsAiDoor
