"""The one way this server reaches ORM: HTTP to ORM's own Rails API, with the caller's Durian OS pass.

The server is secretless: no database, no secret, no credential of its own. Each tool reads the incoming MCP
request's Authorization header, the pass the door checked, and forwards it unchanged to ORM (ORM_API_URL, Puma on
127.0.0.1:3000), which checks it in full, finds the person and judges the route as it does for their screens
(chatwoot/custom/app/services/durian_os/ai/). Requests stay on that host under /api/v1/ or /api/v2/, never follow a
redirect, never keep or send a cookie and ignore proxy settings.

A pass is good for ONE API request (ORM records its jti and refuses it a second time), so one MCP request makes at
most one call here: a second is refused before it is sent.

Every call says `X-Forwarded-Proto: https` and `Accept: application/json`: ORM may force SSL, and a plain-HTTP call
would be redirected, which this server never follows.

ORM answers errors as {"error": ...}, {"message": ...} or {"errors": [...]}; they come back as ToolError in plain
words: 401 a connection that is no longer valid, 403 not allowed (the reason), 404 not found, 400/409/422 refused
(ORM's own words), 429 busy, 502 an outside system did not answer.
"""
import logging
import os
import re
import time
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import unquote

import httpx
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_access_token, get_http_headers, get_http_request

from mcp_server import passes

log = logging.getLogger('orm.mcp')

API_URL = os.environ.get('ORM_API_URL', 'http://127.0.0.1:3000')
#: The ASGI scope key that notes a request has spent its pass on an API call.
SPENT = 'orm.mcp.pass_spent'
#: A path this server may send: /api/v1/ or /api/v2/ and then unreserved characters, sub-delimiters and %XX escapes only.
SAFE_PATH = re.compile(r"^/api/v[12]/(?:[A-Za-z0-9._~!$&'()*+,;=:@/-]|%[0-9A-Fa-f]{2})+$")
#: Seconds for one call: rack-timeout ends a Rails request at 15.
TIMEOUT = 30.0

PASS_INVALID = ('ORM did not accept this connection: your access to ORM may have changed, or the connection has '
                'expired. Check ORM in Durian OS and reconnect.')
NO_PASS = 'No Durian OS connection reached this server. Connect ORM through Durian OS.'
DOUBLED = ("More than one Authorization header reached this server, or not the one it checked. Send exactly one "
           "'Authorization: Bearer ...' header.")
PASS_SPENT = ('This request already used its one-time Durian OS pass on an ORM call, so it cannot make a second. No ORM '
              'tool needs two: tell the ORM team.')
OUTSIDE = "That request would leave ORM's API, so it was not sent."


def new_client(base_url=API_URL, transport=None):
    """The one pooled client, with no credential of its own: the caller's rides on each request only. No cookie is
    kept (a shared jar would hand one caller's cookie to the next), no redirect followed, no proxy setting read."""
    return httpx.AsyncClient(base_url=base_url, timeout=httpx.Timeout(TIMEOUT), follow_redirects=False, trust_env=False,
                             cookies=CookieJar(DefaultCookiePolicy(allowed_domains=[])), transport=transport)


_client = new_client()


def account_id():
    """The one ORM account the tools reach (ORM_ACCOUNT_ID, default 1): the AI never names an account."""
    value = os.environ.get('ORM_ACCOUNT_ID', '1').strip()
    return int(value) if value.isdigit() and int(value) > 0 else 1


def _authorization_values():
    """Every Authorization header of the current MCP request, in order: the door checked the first, so a second one
    must never be the one that is forwarded."""
    try:
        return list(get_http_request().headers.getlist('authorization'))
    except (RuntimeError, AttributeError):  # not over HTTP: an in-process call
        one = get_http_headers(include={'authorization'}).get('authorization')
        return [one] if one else []


def caller_authorization():
    """The request's one Authorization header, exactly the pass the door verified, or a ToolError."""
    values = _authorization_values()
    if len(values) > 1:
        raise ToolError(DOUBLED)
    header = values[0] if values else None
    token = passes.bearer_token(header)
    if not token:
        raise ToolError(NO_PASS)
    checked = get_access_token()
    if checked is not None and token != checked.token:
        raise ToolError(DOUBLED)
    return header


def _spend():
    """Note that this request has used its pass; refuse a second use."""
    try:
        scope = get_http_request().scope
    except RuntimeError:
        return  # not inside an HTTP request (an in-process call)
    if scope.get(SPENT):
        raise ToolError(PASS_SPENT)
    scope[SPENT] = True


def checked_path(path):
    """The path, if it stays inside ORM's API: no dot segments (also none hidden in an escape), no empty segment, no
    query or fragment, nothing but plain characters and %XX escapes."""
    if not isinstance(path, str) or not SAFE_PATH.match(path) or '//' in path:
        raise ToolError(OUTSIDE)
    for segment in path.split('/'):
        if segment in ('.', '..') or any(part in ('.', '..') for part in unquote(segment).replace('\\', '/').split('/')):
            raise ToolError(OUTSIDE)
    return path


def _scalar(value):
    return 'true' if value is True else 'false' if value is False else value


def query_pairs(params):
    """The query as (key, value) pairs, the way Rails reads it: a list goes out as `key[]=a&key[]=b`, a boolean as
    true or false. Empty values are left out."""
    pairs = []
    for key, value in (params or {}).items():
        if isinstance(value, (list, tuple)):
            pairs.extend((f'{key}[]', _scalar(v)) for v in value if v not in (None, ''))
        elif value not in (None, ''):
            pairs.append((key, _scalar(value)))
    return pairs


def _words(value):
    """An `errors` value in words: a list of strings, or {field: [messages]}."""
    if isinstance(value, dict):
        value = [f"{k}: {', '.join(map(str, v)) if isinstance(v, list) else v}" for k, v in value.items()]
    if isinstance(value, list):
        parts = [item if isinstance(item, str) else _words(item) if isinstance(item, dict) else str(item) for item in value[:5]]
        return '; '.join(parts)[:500]
    return str(value)[:500]


def _detail(response):
    try:
        payload = response.json()
    except ValueError:
        return (response.text or '').strip()[:300]
    if isinstance(payload, dict):
        for key in ('error', 'message', 'errors', 'detail'):
            if payload.get(key):
                value = payload[key]
                return value[:500] if isinstance(value, str) else _words(value)
        return str(payload)[:500]
    return str(payload)[:500]


def _log(method, path, status, started):
    token = get_access_token()
    claims = (token.claims or {}) if token is not None else {}
    # Only the route's first segments: never an id or a search after them, never the pass.
    log.info('api %s %s client=%s ref=%s status=%s ms=%d', method, '/'.join(path.split('/')[:4]), passes.client_of(claims),
             str(claims.get('jti') or '')[:6], status, (time.monotonic() - started) * 1000)


async def api(method, path, *, params=None, json=None):
    """One call to ORM's API as the caller. Empty `params` are left out; a POST always sends a JSON body."""
    header = caller_authorization()
    path = checked_path(path)
    _spend()
    query = query_pairs(params)
    if method.upper() != 'GET' and json is None:
        json = {}
    started, status = time.monotonic(), 'failed'
    headers = {'Authorization': header, 'Accept': 'application/json', 'X-Forwarded-Proto': 'https'}
    try:
        response = await _client.request(method, path, params=query or None, json=json, headers=headers)
        status = response.status_code
    except httpx.RequestError as exc:
        raise ToolError(f'ORM is not answering ({exc.__class__.__name__}); try again in a minute.') from exc
    finally:
        _log(method, path, status, started)
    code = response.status_code
    if 300 <= code < 400:
        raise ToolError('ORM answered with a redirect, which this connection never follows.')
    if code == 401:
        raise ToolError(PASS_INVALID)
    if code == 403:
        raise ToolError(f'Not allowed: {_detail(response)}')
    if code == 404:
        raise ToolError(f'Not found: {_detail(response)}')
    if code == 429:
        raise ToolError(f'ORM is busy: {_detail(response)}')
    if code in (400, 409, 413, 422):
        raise ToolError(f'Refused by ORM: {_detail(response)}')
    if code == 502:
        raise ToolError(_detail(response) or 'An outside system did not answer ORM.')
    if code >= 500:
        raise ToolError(f'ORM had a problem ({code}): {_detail(response)}')
    if code >= 400:
        raise ToolError(f'ORM answered {code}: {_detail(response)}')
    if code == 204 or not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise ToolError('ORM answered something that is not JSON.') from exc


async def close():
    await _client.aclose()
