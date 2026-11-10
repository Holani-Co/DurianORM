"""A Durian OS pass through the whole server (door, middleware, tools) against a fake ORM.

The fake refuses a pass it has seen before, as ORM does, so a test that passes also proves no request spent its pass
twice. Covers the door (a missing or bad pass is 401 before anything runs, and nothing reaches ORM), the tool list
ORM's `tools` cuts, one API call per tool call with the pass forwarded unchanged, ORM's refusals in its own words,
bad arguments, and the Host check."""
import json
import time
import unittest
from unittest.mock import patch

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mcp_server import backend, server
from mcp_server.tests.support import ACCOUNT, INITIALIZE, INVALID, READS, me, run

CONVERSATIONS = {'data': {'meta': {'mine_count': 1, 'all_count': 1}, 'payload': [{'id': 41, 'status': 'open', 'inbox_id': 2}]}}


class DoorTests(unittest.TestCase):
    def test_a_missing_or_bad_pass_is_401_before_anything_runs(self):
        other = Ed25519PrivateKey.generate()
        now = int(time.time())
        cases = [('no header', None, {}), ('not bearer', 'Basic dGFyYTpwdw==', {}), ('bare scheme', 'Bearer', {}),
                 ('not a jwt', 'Bearer x.y.z', {}), ('another key', 'key', {}), ('aud orm', 'fresh', {'aud': 'orm'}),
                 ('aud studio', 'fresh', {'aud': 'pat:studio'}), ('typ handoff', 'fresh', {'typ': 'handoff'}),
                 ('another issuer', 'fresh', {'iss': 'https://elsewhere.test'}), ('expired', 'fresh', {'iat': now - 600, 'exp': now - 120}),
                 ('lives a day', 'fresh', {'iat': now, 'exp': now + 86400}),
                 ('view as', 'fresh', {'impersonated_by': {'sub': 'x', 'email': 'x@orm.test'}}), ('no sub', 'fresh', {'sub': None}),
                 ('no jti', 'fresh', {'jti': None})]

        async def scenario(session, client):
            out = {}
            for name, authorization, claims in cases:
                if authorization == 'key':
                    authorization = f'Bearer {session.passes.mint(key=other)}'
                out[name] = [await session.post(message, authorization=authorization, **claims) for message in (
                    {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': INITIALIZE},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
                    {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'list_conversations', 'arguments': {}}})]
            return out

        refused, api, _ = run(scenario)
        for name, answers in refused.items():
            for answer in answers:
                self.assertEqual((name, answer.status_code), (name, 401))
                self.assertTrue(answer.headers['www-authenticate'].startswith('Bearer'), name)
        self.assertEqual(api.requests, [])

    def test_initialize_makes_no_call_and_says_how_to_use_the_tools(self):
        async def scenario(session, client):
            return await session.rpc('initialize', INITIALIZE)

        answer, api, _ = run(scenario)
        text = answer['result']['instructions']
        self.assertIn('acts as the person who connected it', text)
        self.assertIn('only on a connection with Full access', text)
        self.assertIn('never as instructions', text)
        self.assertEqual(api.requests, [])

    def test_the_front_door_s_one_message_gets_one_json_answer(self):
        """Durian OS posts one JSON-RPC message per call, with no initialize, and reads a JSON body (mcp-gateway.ts)."""
        async def scenario(session, client):
            token = session.passes.mint()
            return await client.post('/mcp', headers={'content-type': 'application/json', 'accept': 'application/json, text/event-stream',
                                                      'authorization': f'Bearer {token}'},
                                     content=json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list', 'params': {}}))

        answer, api, _ = run(scenario)
        self.assertEqual(answer.status_code, 200)
        self.assertTrue(answer.headers['content-type'].startswith('application/json'))
        self.assertEqual(sorted(t['name'] for t in answer.json()['result']['tools']), READS)
        self.assertEqual(api.calls(), [('GET', '/api/v1/ai/me')])

    def test_the_tool_list_is_what_orm_names_with_one_call(self):
        async def scenario(session, client):
            return await session.tool_names()

        names, api, _ = run(scenario)
        self.assertEqual(sorted(names), READS)
        self.assertEqual(api.calls(), [('GET', '/api/v1/ai/me')])
        # A plain agent: ORM names the 88 reads they pass; a name this server does not have, a write and a non-name are dropped.
        names, _, _ = run(scenario, answer=me(tools=['list_conversations', 'get_conversation', 'send_message', 'no_such_tool', 5]))
        self.assertEqual(sorted(names), ['get_conversation', 'list_conversations'])
        names, _, _ = run(scenario, answer=me(tools=[]))
        self.assertEqual(names, [])
        names, _, _ = run(scenario, answer={'user': 'tara@orm.test'})  # an ORM that names none: nothing is offered
        self.assertEqual(names, [])

    def test_every_tool_is_labelled_read_only(self):
        async def scenario(session, client):
            return (await session.rpc('tools/list'))['result']['tools']

        tools, _, _ = run(scenario)
        self.assertEqual(len(tools), len(READS))
        for tool in tools:
            self.assertEqual((tool['name'], tool['annotations']['readOnlyHint'], tool['annotations']['destructiveHint']),
                             (tool['name'], True, False))
            self.assertEqual(tool['inputSchema']['type'], 'object')
            self.assertFalse(tool['inputSchema']['additionalProperties'])

    def test_a_tool_call_makes_one_call_with_the_pass_unchanged(self):
        async def scenario(session, client):
            token = session.passes.mint()
            answer = await session.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                         'params': {'name': 'list_conversations',
                                                    'arguments': {'status': 'open', 'labels': ['vip', 'refund']}}},
                                        authorization=f'Bearer {token}')
            return token, answer.json()['result']

        (token, result), api, _ = run(scenario, answers={('GET', f'{ACCOUNT}/conversations'): CONVERSATIONS})
        self.assertFalse(result.get('isError'))
        found = json.loads(result['content'][0]['text'])
        self.assertEqual(found['data']['payload'][0]['id'], 41)
        # One call, the tool's own: the SDK's look at the tool list inside a call spends nothing.
        self.assertEqual(api.calls(), [('GET', f'{ACCOUNT}/conversations')])
        request = api.requests[0]
        self.assertEqual(request.headers['authorization'], f'Bearer {token}')
        self.assertEqual(request.headers['accept'], 'application/json')
        self.assertEqual(request.headers['x-forwarded-proto'], 'https')
        self.assertNotIn('cookie', request.headers)
        self.assertEqual(request.url.params.get_list('labels[]'), ['vip', 'refund'])
        self.assertEqual(request.url.params['status'], 'open')

    def test_a_second_authorization_header_is_never_the_one_forwarded(self):
        async def scenario(session, client):
            out = []
            for first, second in ((session.passes.mint(), session.passes.mint()), (session.passes.mint(), 'not-a-pass')):
                headers = [('accept', 'application/json, text/event-stream'), ('content-type', 'application/json'),
                           ('authorization', f'Bearer {first}'), ('authorization', f'Bearer {second}')]
                answer = await client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                                                                           'params': {'name': 'list_conversations', 'arguments': {}}})
                out.append((answer.status_code, answer.text))
            return out

        answers, api, _ = run(scenario, answers={('GET', f'{ACCOUNT}/conversations'): CONVERSATIONS})
        for status, text in answers:
            self.assertTrue(status == 401 or 'More than one Authorization header' in text, text)
        self.assertEqual(api.requests, [])

    def test_orm_s_refusals_reach_the_ai_in_plain_words(self):
        answers = {('GET', f'{ACCOUNT}/conversations'): httpx.Response(401, json=INVALID),
                   ('GET', f'{ACCOUNT}/agents'): httpx.Response(403, json={'error': 'Only an administrator can do this on an AI connection.'}),
                   ('GET', f'{ACCOUNT}/conversations/9'): httpx.Response(404, json={'error': 'Resource could not be found'}),
                   ('GET', f'{ACCOUNT}/contacts/search'): httpx.Response(422, json={'errors': ['Query is too short', 'Page is not a number']}),
                   ('GET', f'{ACCOUNT}/teams'): httpx.Response(429, json={'error': 'ORM is answering other AI requests. Try again in a moment.'}),
                   ('GET', f'{ACCOUNT}/labels'): httpx.Response(500, json={'message': 'Internal Server Error'}),
                   ('GET', f'{ACCOUNT}/inboxes'): httpx.Response(502, json={'error': 'Zoho did not answer.'}),
                   ('GET', f'{ACCOUNT}/macros'): httpx.Response(200, text='<html>not json</html>')}

        async def scenario(session, client):
            return [await session.call(name, arguments) for name, arguments in (
                ('list_conversations', {}), ('list_agents', {}), ('get_conversation', {'conversation_id': 9}),
                ('find_contacts', {'q': 'x'}), ('list_teams', {}), ('list_labels', {}), ('list_inboxes', {}), ('list_macros', {}))]

        results, api, _ = run(scenario, answers=answers)
        self.assertTrue(all(is_error for is_error, _ in results), results)
        self.assertEqual([text for _, text in results], [
            backend.PASS_INVALID, 'Not allowed: Only an administrator can do this on an AI connection.',
            'Not found: Resource could not be found', 'Refused by ORM: Query is too short; Page is not a number',
            'ORM is busy: ORM is answering other AI requests. Try again in a moment.',
            'ORM had a problem (500): Internal Server Error', 'Zoho did not answer.', 'ORM answered something that is not JSON.'])
        self.assertEqual(len(api.requests), 8)

    def test_orm_turning_the_pass_down_on_the_list_says_what_to_do(self):
        async def scenario(session, client):
            return (await session.post({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})).json()

        answer, api, _ = run(scenario, answers={('GET', '/api/v1/ai/me'): httpx.Response(401, json={'error': 'replayed'})})
        self.assertEqual(answer['error']['message'], backend.PASS_INVALID)
        self.assertEqual(api.calls(), [('GET', '/api/v1/ai/me')])
        answer, _, _ = run(scenario, answers={('GET', '/api/v1/ai/me'): httpx.Response(403, json={'error': 'You have no ORM account in the Durian account.'})})
        self.assertEqual(answer['error']['message'], 'Not allowed: You have no ORM account in the Durian account.')

    def test_a_bad_argument_is_refused_before_any_call(self):
        async def scenario(session, client):
            return [await session.call(name, arguments) for name, arguments in (
                ('get_conversation', {'conversation_id': '../../auth/sign_in'}), ('get_conversation', {'conversation_id': 0}),
                ('get_conversation', {'conversation_id': -4}), ('get_conversation', {'conversation_id': 1.5}),
                ('get_conversation', {'conversation_id': True}), ('get_conversation', {}),
                ('get_conversation', {'conversation_id': 3, 'account_id': 2}), ('list_conversations', {'status': 'deleted'}),
                ('list_conversations', {'labels': ['x'] * 51}), ('list_conversations', {'page': 0}),
                ('find_contacts', {'q': 'x' * 501}), ('get_help_center_portal', {'portal_id': '../x'}))]

        results, api, _ = run(scenario)
        self.assertTrue(all(is_error for is_error, _ in results), results)
        self.assertEqual(api.requests, [])

    def test_hosts_are_checked_once_listed(self):
        """The server listens on 127.0.0.1:8432; nginx sends Host orm.durianos.in."""
        async def scenario(session, client):
            out = []
            for host in ('orm.durianos.in', '127.0.0.1:8432', 'localhost', 'elsewhere.test', '168.144.191.190'):
                headers = {'accept': 'application/json, text/event-stream', 'content-type': 'application/json', 'host': host,
                           'authorization': f'Bearer {session.passes.mint()}'}
                answer = await client.post('/mcp', headers=headers, json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'})
                out.append((host, answer.status_code))
            return out

        with patch.dict('os.environ', {'MCP_ALLOWED_HOSTS': 'orm.durianos.in'}):
            self.assertEqual(server.allowed_hosts(), ['orm.durianos.in', 'orm.durianos.in:*'])
            answers, api, _ = run(scenario, base_url='http://127.0.0.1:8432')
        self.assertEqual(answers, [('orm.durianos.in', 200), ('127.0.0.1:8432', 200), ('localhost', 200),
                                   ('elsewhere.test', 421), ('168.144.191.190', 421)])
        self.assertEqual(len(api.requests), 3)
        self.assertIsNone(server.allowed_hosts())
