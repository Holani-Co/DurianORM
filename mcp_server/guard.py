"""Which tools a person is offered. ORM is the only judge of every call; this only shapes the menu, from
what ORM itself says.

- `ListByAccess`: a client's tools/list is cut to the tools ORM names for this person, read from one
  `GET /api/v1/ai/me` made with the request's own pass (its `tools`: the registry's reads for their access after the
  pass's ceiling).
- `JsonRpcMethod` notes which JSON-RPC method a POST to /mcp carries. The MCP SDK also lists the tools itself inside a
  tools/call, and that list runs the same middleware: only a client's own tools/list may spend the request's pass on
  /api/v1/ai/me, because a tools/call needs it for its own API call.

Ported from Content Studio's mcp_server/guard.py.
"""
import json
import logging

from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers, get_http_request
from fastmcp.server.middleware import Middleware
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from starlette.responses import PlainTextResponse

from mcp_server import backend

log = logging.getLogger('orm.mcp')

ME_PATH = '/api/v1/ai/me'
MCP_PATH = '/mcp'
#: The ASGI scope key that holds the JSON-RPC method of a request.
METHOD = 'orm.mcp.method'
#: The largest JSON-RPC request read in full to find its method. No tool takes more than a list of names.
MAX_BODY = 256 * 1024
SERVER_ERROR = -32000


def listed(name, me):
    """Whether the person `me` describes is offered the tool `name`: exactly the tools ORM names."""
    named = me.get('tools') if isinstance(me, dict) else None
    return isinstance(named, list) and name in {n for n in named if isinstance(n, str)}


class ListByAccess(Middleware):
    """A client's tools/list shows the tools the person may call."""

    async def on_list_tools(self, context, call_next):
        tools = await call_next(context)
        try:
            scope = get_http_request().scope
        except RuntimeError:
            return tools  # not over HTTP: an in-process call, which has no person to ask about
        if scope.get(METHOD) != 'tools/list':
            return tools  # the SDK reading definitions inside a call: its pass is the call's
        if not get_http_headers(include={'authorization'}).get('authorization'):
            return []
        try:
            me = await backend.api('GET', ME_PATH)
        except ToolError as exc:
            raise McpError(ErrorData(code=SERVER_ERROR, message=str(exc))) from exc
        if not isinstance(me, dict):
            raise McpError(ErrorData(code=SERVER_ERROR, message='ORM did not say who this connection is.'))
        unknown = {n for n in me.get('tools') or [] if isinstance(n, str)} - {t.name for t in tools}
        if unknown:
            log.warning('ORM names tools this server does not have: %s', ', '.join(sorted(unknown)))
        return [tool for tool in tools if listed(tool.name, me)]


def _method_of(body):
    try:
        message = json.loads(body)
    except ValueError:
        return None
    method = message.get('method') if isinstance(message, dict) else None
    return method if isinstance(method, str) else None


class JsonRpcMethod:
    """Notes the JSON-RPC method of each POST to /mcp (METHOD) and hands the body on unchanged. Only a request that
    passed the door is read, so nobody unauthenticated makes it buffer a body."""

    def __init__(self, app, path=MCP_PATH):
        self.app, self.path = app, path

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope.get('path') != self.path \
                or not getattr(scope.get('user'), 'is_authenticated', False):
            await self.app(scope, receive, send)  # FastMCP answers it, with 401 when the pass is missing or bad
            return
        for name, value in scope.get('headers') or ():
            if name.lower() == b'content-length' and value.strip().isdigit() and int(value) > MAX_BODY:
                await PlainTextResponse('Request too large', status_code=413)(scope, receive, send)
                return
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] != 'http.request':
                return
            chunk = message.get('body', b'')
            size += len(chunk)
            if size > MAX_BODY:
                await PlainTextResponse('Request too large', status_code=413)(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
        body = b''.join(chunks)
        scope[METHOD] = _method_of(body)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()

        await self.app(scope, replay, send)
