"""The MCP server over real sockets, in front of a fake Rails that also behaves like ORM's door: Durian OS's front door
posts one JSON-RPC message per call with a fresh pass, the server (uvicorn, 127.0.0.1) checks it and calls the fake
Rails (uvicorn, 127.0.0.1) with the same pass, and the fake Rails checks the pass's signature and refuses a pass it has
seen, a call without `X-Forwarded-Proto: https`, a cookie and any route it was not told about. Nothing leaves this
computer; the people are invented."""
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

import httpx
import jwt
import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_server import backend, server
from mcp_server.tests.support import ACCOUNT, MCP_HEADERS, PERSON, READS, Passes, me

PLAIN_AGENT_TOOLS = ['get_my_orm_access', 'list_conversations', 'get_conversation', 'list_messages', 'find_contacts', 'get_contact']
CONVERSATION = {'id': 41, 'status': 'open', 'meta': {'sender': {'name': 'Omar Quill'}}}
MESSAGES = {'payload': [{'id': 1, 'content': 'Is the Aster sofa in stock?', 'message_type': 0,
                         'content_attributes': {'email': {'subject': 'Aster sofa', 'html_content': {'full': '<p>big</p>'}}}}]}


class FakeRailsApp:
    """ORM's Rails as an ASGI app: verifies the pass the way the door does (signature, audience, one use)."""

    def __init__(self, minted):
        self.minted, self.seen, self.log = minted, set(), []
        self.routes = {
            ('GET', '/api/v1/ai/me'): lambda q, b: me(tools=PLAIN_AGENT_TOOLS),
            ('GET', f'{ACCOUNT}/conversations'): lambda q, b: {'data': {'payload': [CONVERSATION], 'query': q}},
            ('GET', f'{ACCOUNT}/conversations/41'): lambda q, b: CONVERSATION,
            ('GET', f'{ACCOUNT}/conversations/41/messages'): lambda q, b: MESSAGES,
            ('GET', f'{ACCOUNT}/agents'): lambda q, b: JSONResponse({'error': 'Only an administrator can do this.'}, status_code=403),
        }
        self.app = Starlette(routes=[Route('/{path:path}', self.handle, methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE'])])

    async def handle(self, request):
        path = request.url.path
        header = request.headers.get('authorization', '')
        self.log.append((request.method, path, dict(request.headers), request.url.query))
        if request.headers.get('x-forwarded-proto') != 'https':
            return JSONResponse({'error': 'redirect to https'}, status_code=301, headers={'location': 'https://orm.test' + path})
        try:
            claims = jwt.decode(header[7:], self.minted.key.public_key(), algorithms=['EdDSA'], issuer='https://durianos.in', audience='pat:orm',
                                leeway=30)
        except jwt.PyJWTError:
            return JSONResponse({'error': 'This Durian OS connection is not valid. Reconnect in Durian OS.'}, status_code=401)
        if claims['jti'] in self.seen or 'cookie' in request.headers or claims['sub'] != PERSON:
            return JSONResponse({'error': 'This Durian OS connection is not valid. Reconnect in Durian OS.'}, status_code=401)
        self.seen.add(claims['jti'])
        answer = self.routes.get((request.method, path))
        if answer is None:
            return JSONResponse({'error': 'Resource could not be found'}, status_code=404)
        answer = answer(dict(request.query_params), None)
        return answer if not isinstance(answer, dict) else JSONResponse(answer)


class Running:
    """A uvicorn server in a thread, on a free port of 127.0.0.1."""

    def __init__(self, app):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=self.port, log_level='critical', lifespan='on'))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        deadline = time.time() + 20
        while not self.server.started and time.time() < deadline:
            time.sleep(0.05)
        assert self.server.started, 'the test server did not start'
        return self

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)

    @property
    def url(self):
        return f'http://127.0.0.1:{self.port}'


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.minted = Passes()
        self.rails = FakeRailsApp(self.minted)
        self.stack = []

    def tearDown(self):
        for running in reversed(self.stack):
            running.__exit__(None, None, None)

    def start(self):
        rails = Running(self.rails.app)
        rails.__enter__()
        self.stack.append(rails)
        client = backend.new_client(base_url=rails.url)
        patches = [patch.object(server.mcp, 'auth', self.minted.verifier()), patch.object(backend, '_client', client)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        mcp = Running(server.http_app())
        mcp.__enter__()
        self.stack.append(mcp)
        return httpx.Client(base_url=mcp.url, timeout=20)

    def post(self, client, message, **claims):
        headers = {**MCP_HEADERS, 'authorization': f'Bearer {self.minted.mint(**claims)}'}
        return client.post('/mcp', headers=headers, json=message)

    def call(self, client, name, arguments=None, **claims):
        answer = self.post(client, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments or {}}}, **claims)
        self.assertEqual(answer.status_code, 200, answer.text)
        result = answer.json()['result']
        return result.get('isError', False), ''.join(block.get('text', '') for block in result['content'])

    def test_a_plain_agent_lists_only_what_orm_names_and_reads_a_conversation(self):
        client = self.start()
        listed = self.post(client, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(sorted(t['name'] for t in listed.json()['result']['tools']), sorted(PLAIN_AGENT_TOOLS))
        self.assertLess(len(PLAIN_AGENT_TOOLS), len(READS))

        is_error, text = self.call(client, 'get_conversation', {'conversation_id': 41})
        self.assertEqual((is_error, json.loads(text)['meta']['sender']['name']), (False, 'Omar Quill'))

        is_error, text = self.call(client, 'list_conversations', {'status': 'open', 'labels': ['vip', 'refund'], 'page': 2})
        self.assertFalse(is_error)
        # one call per request, and the list made exactly one /ai/me call: 3 requests in all, each with its own pass
        self.assertEqual([(m, p) for m, p, _, _ in self.rails.log], [('GET', '/api/v1/ai/me'), ('GET', f'{ACCOUNT}/conversations/41'),
                                                                     ('GET', f'{ACCOUNT}/conversations')])
        query = self.rails.log[-1][3]
        self.assertIn('labels%5B%5D=vip', query)
        self.assertIn('labels%5B%5D=refund', query)
        self.assertIn('page=2', query)
        for _, _, headers, _ in self.rails.log:
            self.assertNotIn('cookie', headers)
            self.assertEqual(headers['accept'], 'application/json')

    def test_email_html_is_left_out_of_messages(self):
        client = self.start()
        is_error, text = self.call(client, 'list_messages', {'conversation_id': 41})
        self.assertFalse(is_error)
        self.assertEqual(json.loads(text)['payload'][0]['content_attributes']['email'], {'subject': 'Aster sofa'})

    def test_orm_s_refusal_is_the_persons_role_and_says_so(self):
        client = self.start()
        is_error, text = self.call(client, 'list_agents')
        self.assertEqual((is_error, text), (True, 'Not allowed: Only an administrator can do this.'))

    def test_no_pass_or_a_bad_one_is_401_and_nothing_reaches_rails(self):
        client = self.start()
        message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'list_conversations', 'arguments': {}}}
        self.assertEqual(client.post('/mcp', headers=MCP_HEADERS, json=message).status_code, 401)
        self.assertEqual(client.post('/mcp', headers={**MCP_HEADERS, 'authorization': 'Bearer junk.junk.junk'}, json=message).status_code, 401)
        self.assertEqual(self.post(client, message, aud='pat:studio').status_code, 401)
        self.assertEqual(self.post(client, message, sub=None).status_code, 401)
        self.assertEqual(self.rails.log, [])

    def test_a_pass_replayed_is_refused_by_rails_and_reported_as_a_lost_connection(self):
        client = self.start()
        token = self.minted.mint()
        message = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'get_conversation', 'arguments': {'conversation_id': 41}}}
        headers = {**MCP_HEADERS, 'authorization': f'Bearer {token}'}
        first = client.post('/mcp', headers=headers, json=message).json()['result']
        second = client.post('/mcp', headers=headers, json=message).json()['result']
        self.assertFalse(first.get('isError'))
        self.assertTrue(second['isError'])
        self.assertEqual(second['content'][0]['text'], backend.PASS_INVALID)

    def test_a_route_rails_does_not_know_is_not_found(self):
        client = self.start()
        is_error, text = self.call(client, 'get_contact', {'contact_id': 99})
        self.assertEqual((is_error, text), (True, 'Not found: Resource could not be found'))
