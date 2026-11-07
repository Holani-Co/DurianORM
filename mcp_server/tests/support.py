"""What the MCP server's tests share: a throwaway Ed25519 key standing in for Durian OS's, minting passes; ORM's Rails
API as the server sees it (a fake that refuses a pass it has seen, as the real one does, and a call that does not say
it came over https); and the real server app, driven in-process through its door, middleware, backend client and
tools. Nothing here reaches a network except test_end_to_end.py, which uses sockets on 127.0.0.1. The people are
invented.

Run from the repository root, with mcp_server/requirements.txt and requirements-test.txt installed:
    python -m pytest mcp_server/tests
"""
import asyncio
import base64
import contextlib
import json
import os
import time
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from mcp_server import backend, passes, server, tools

ISSUER = 'https://durianos.in'
API = 'http://orm.test:3000'
PERSON = '7c1e4b2a-3d5f-4a6b-8c7d-9e0f1a2b3c4d'
MCP_HEADERS = {'accept': 'application/json, text/event-stream', 'content-type': 'application/json'}
INITIALIZE = {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'tests', 'version': '1'}}
EXPORT = tools.load()
SPECS = {t['name']: t for t in EXPORT['tools']}
READS = sorted(SPECS)
INVALID = {'error': 'This Durian OS connection is not valid. Reconnect in Durian OS.'}
ACCOUNT = '/api/v1/accounts/1'


def b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b'=').decode()


class Passes:
    """A throwaway Ed25519 key standing in for Durian OS's, and the passes it signs."""

    def __init__(self, kid='test-os-key'):
        self.key, self.kid = Ed25519PrivateKey.generate(), kid

    def jwks(self):
        public = self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return json.dumps({'keys': [{'kty': 'OKP', 'crv': 'Ed25519', 'kid': self.kid, 'x': b64url(public), 'alg': 'EdDSA', 'use': 'sig'}]})

    def claims(self, **change):
        now = int(time.time())
        claims = {'iss': ISSUER, 'aud': 'pat:orm', 'typ': 'pat', 'sub': PERSON, 'email': 'tara@orm.test', 'name': 'Tara Fern',
                  'groups': [], 'max_level': 'administer', 'client': 'Claude', 'iat': now, 'exp': now + 60, 'jti': b64url(os.urandom(16))}
        claims.update(change)
        return {k: v for k, v in claims.items() if v is not None}

    def mint(self, *, key=None, kid=None, algorithm='EdDSA', header=None, **change):
        return jwt.encode(self.claims(**change), key or self.key, algorithm=algorithm, headers={'kid': kid or self.kid, **(header or {})})

    def verifier(self):
        return passes.PassVerifier(passes.parse_keys(json.loads(self.jwks())), ISSUER)


def me(tools=None, **change):
    """GET /api/v1/ai/me as ORM answers a pass (chatwoot/custom/app/controllers/api/v1/ai_controller.rb)."""
    out = {'user': 'tara@orm.test', 'name': 'Tara Fern', 'user_id': 7, 'os_person_id': PERSON, 'auth_kind': 'pass', 'client': 'Claude',
           'account': {'id': 1, 'name': 'Durian'}, 'ceiling': 'administer', 'role': 'administrator', 'custom_role': None,
           'acts_as': 'administrator', 'permissions': ['administrator'], 'inboxes': [], 'teams': [], 'read_only': True,
           'tools': READS if tools is None else tools}
    out.update(change)
    return out


class FakeRails:
    """ORM's Rails API as the server sees it. Records every request. A pass is single use: one seen before is answered
    401, as ORM does, so a test that passes also proves no request spent its pass twice. A call that does not say
    `X-Forwarded-Proto: https` and `Accept: application/json` is answered with a redirect, as ORM does under FORCE_SSL.
    GET /api/v1/ai/me answers `me`; any other (method, path) what `answers` holds (a body, a Response, or a function
    of (query, body)); anything else 404."""

    def __init__(self, me_answer=None, answers=None):
        self.requests, self.seen = [], set()
        self.me, self.answers = me_answer if me_answer is not None else me(), dict(answers or {})
        self.transport = httpx.MockTransport(self)

    def __call__(self, request):
        self.requests.append(request)
        if request.headers.get('x-forwarded-proto') != 'https':
            return httpx.Response(301, headers={'location': 'https://orm.test' + request.url.path})
        header = request.headers.get('authorization', '')
        token = header[7:] if header.startswith('Bearer ') else ''
        if not token:
            return httpx.Response(401, json=INVALID)
        jti = jwt.decode(token, options={'verify_signature': False}).get('jti')
        if jti in self.seen:
            return httpx.Response(401, json=INVALID)
        self.seen.add(jti)
        key = (request.method, request.url.path)
        answer = self.answers.get(key, self.me if key == ('GET', '/api/v1/ai/me') else None)
        if callable(answer):
            answer = answer(request.url.params, json.loads(request.content) if request.content else None)
        if isinstance(answer, httpx.Response):
            return answer
        if answer is None:
            return httpx.Response(404, json={'error': 'Resource could not be found'})
        return httpx.Response(200, json=answer)

    def calls(self):
        return [(r.method, r.url.path) for r in self.requests]


class McpSession:
    """JSON-RPC to the server over HTTP, in-process. Each request carries a fresh pass unless told otherwise."""

    def __init__(self, client, minted):
        self.client, self.passes, self.next_id = client, minted, 0

    async def post(self, message, *, authorization='fresh', **claims):
        headers = dict(MCP_HEADERS)
        if authorization == 'fresh':
            headers['authorization'] = f'Bearer {self.passes.mint(**claims)}'
        elif authorization is not None:
            headers['authorization'] = authorization
        return await self.client.post('/mcp', headers=headers, json=message)

    async def rpc(self, method, params=None, **options):
        self.next_id += 1
        message = {'jsonrpc': '2.0', 'id': self.next_id, 'method': method, **({'params': params} if params is not None else {})}
        answer = await self.post(message, **options)
        assert answer.status_code == 200, (answer.status_code, answer.text)
        return answer.json()

    async def tool_names(self, **options):
        return [t['name'] for t in (await self.rpc('tools/list', **options))['result']['tools']]

    async def call(self, name, arguments=None, **options):
        """(is_error, text) of one tools/call."""
        result = (await self.rpc('tools/call', {'name': name, 'arguments': arguments or {}}, **options))['result']
        return result.get('isError', False), ''.join(block.get('text', '') for block in result['content'])


@contextlib.contextmanager
def sidecar(minted, api):
    """The real server app (door, middleware, tools) pointed at `api`, with `minted`'s key as Durian OS's."""
    client = httpx.AsyncClient(transport=api.transport, base_url=API, follow_redirects=False)
    with patch.object(server.mcp, 'auth', minted.verifier()), patch.object(backend, '_client', client):
        yield server.http_app()


def serve(app, minted, scenario, base_url='http://mcp.test'):
    """Run `scenario(session, client)` against `app` with its lifespan running, and return what it returns."""
    async def main():
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=base_url) as client:
                return await scenario(McpSession(client, minted), client)
    return asyncio.run(main())


def run(scenario, *, answer=None, answers=None, base_url='http://mcp.test'):
    """(what the scenario returned, the fake Rails, the passes) for one scenario against the real server."""
    minted, api = Passes(), FakeRails(answer, answers)
    with sidecar(minted, api) as app:
        return serve(app, minted, scenario, base_url), api, minted
