"""The tools built from tools.json: schemas, argument checks, the route each one calls, the body a POST read sends,
the way arrays and booleans reach Rails, the account that is never the AI's, and answers cut to fit."""
import json
import re
import unittest
from unittest.mock import patch

import httpx

from mcp_server import backend, tools
from mcp_server.tests.support import ACCOUNT, EXPORT, READS, SPECS, me, run

EMPTY = {'payload': []}


async def _call_all(session, client):
    """Every read once, with the simplest arguments its schema accepts."""
    out = {}
    for name, spec in SPECS.items():
        out[name] = await session.call(name, sample_arguments(spec))
    return out


def sample_arguments(spec):
    values = {}
    for arg in spec['args']:
        if not arg.get('required'):
            continue
        kind = arg['type']
        values[arg['name']] = (arg['enum'][0] if arg.get('enum') else 7 if kind == 'integer' else 1.5 if kind == 'number' else
                               True if kind == 'boolean' else [{'attribute_key': 'status'}] if kind == 'array' and arg.get('items') == 'object' else
                               [3] if kind == 'array' and arg.get('items') == 'integer' else ['a'] if kind == 'array' else {'a': 1} if kind == 'object' else
                               'abc123' if not arg.get('pattern') else '12345')
    return values


class SchemaTests(unittest.TestCase):
    def test_each_read_is_a_tool_with_the_registry_s_words(self):
        built = {t.name: t for t in (tools.make_tool(spec) for spec in EXPORT['tools'])}
        self.assertEqual(sorted(built), READS)
        for name, spec in SPECS.items():
            self.assertEqual((built[name].title, built[name].description), (spec['title'], spec['description']))
            self.assertTrue(built[name].annotations.readOnlyHint)

    def test_the_account_is_never_an_argument(self):
        for name, spec in SPECS.items():
            properties = tools.make_tool(spec).parameters['properties']
            for forbidden in ('account_id', 'id_of_account', 'account'):
                self.assertNotIn(forbidden, properties, name)
            if spec['account_param'] == 'id':
                self.assertNotIn('id', properties, name)  # get_account_settings has no argument at all

    def test_the_schema_follows_the_registry_args(self):
        schema = tools.make_tool(SPECS['list_conversations']).parameters
        self.assertEqual(schema['properties']['status']['enum'], ['open', 'resolved', 'pending', 'snoozed', 'all'])
        self.assertEqual(schema['properties']['labels'], {'type': 'array', 'items': {'type': 'string', 'maxLength': 500}, 'maxItems': 50,
                                                          'description': 'Only conversations with any of these labels.'})
        self.assertEqual(schema['properties']['page']['minimum'], 1)
        self.assertEqual(schema['required'], [])
        one = tools.make_tool(SPECS['get_conversation']).parameters
        self.assertEqual((one['required'], one['properties']['conversation_id']['minimum']), (['conversation_id'], 1))
        portal = tools.make_tool(SPECS['get_help_center_portal']).parameters['properties']['portal_id']
        self.assertEqual((portal['type'], portal['pattern']), ('string', r'^[a-z0-9-]{1,100}$'))

    def test_list_messages_has_one_local_argument_that_is_never_sent(self):
        schema = tools.make_tool(SPECS['list_messages']).parameters
        self.assertIn('include_html', schema['properties'])
        self.assertNotIn('include_html', [a['name'] for a in SPECS['list_messages']['args']])


class CallTests(unittest.TestCase):
    def test_every_read_calls_its_own_route_once_with_the_account_filled_in(self):
        results, api, _ = run(_call_all, answer=me())
        self.assertEqual(len(api.requests), len(SPECS))
        for request, (name, spec) in zip(api.requests, SPECS.items()):
            self.assertEqual(request.method, spec['method'], name)
            self.assertNotIn(':', request.url.path, name)
            expected = re.sub(r':account_id|:id(?=/|$)' if spec['account_param'] == 'id' else r':account_id', '1', spec['path'])
            segments = expected.split('/')
            actual = request.url.path.split('/')
            self.assertEqual(len(actual), len(segments), name)
            for want, got in zip(segments, actual):
                if not want.startswith(':'):
                    self.assertEqual(got, want, name)
        # Nothing here is a server error or a refused argument: every sample call got through to the fake (404 for a route
        # it does not know is the tool reporting 'Not found').
        for name, (is_error, text) in results.items():
            self.assertTrue(not is_error or text.startswith('Not found'), (name, text))

    def test_path_ids_query_and_body_go_where_the_registry_says(self):
        answers = {('GET', f'{ACCOUNT}/conversations/41'): {'id': 41},
                   ('GET', f'{ACCOUNT}/conversations/41/messages'): {'payload': []},
                   ('POST', f'{ACCOUNT}/conversations/filter'): {'payload': []},
                   ('POST', f'{ACCOUNT}/contact_inboxes/filter'): {'id': 5},
                   ('GET', '/api/v1/accounts/1'): {'id': 1},
                   ('GET', f'{ACCOUNT}/portals/help-center/articles/9'): {'id': 9},
                   ('GET', '/api/v2/accounts/1/reports/summary'): {'conversations_count': 3}}

        async def scenario(session, client):
            filters = [{'attribute_key': 'status', 'filter_operator': 'equal_to', 'values': ['open'], 'query_operator': None}]
            return [await session.call(name, arguments) for name, arguments in (
                ('get_conversation', {'conversation_id': 41}), ('list_messages', {'conversation_id': 41, 'before': 900}),
                ('filter_conversations', {'payload': filters, 'page': 2}),
                ('find_contact_by_source_id', {'inbox_id': 3, 'source_id': '+919800000000'}), ('get_account_settings', {}),
                ('get_help_center_article', {'portal_id': 'help-center', 'article_id': 9}),
                ('get_report_summary', {'since': 1762000000, 'until': 1762600000, 'business_hours': False, 'type': 'account'}))]

        results, api, _ = run(scenario, answers=answers)
        self.assertEqual([is_error for is_error, _ in results], [False] * 7, results)
        by_path = {(r.method, r.url.path): r for r in api.requests}
        self.assertEqual(by_path[('GET', f'{ACCOUNT}/conversations/41/messages')].url.params['before'], '900')
        filtered = by_path[('POST', f'{ACCOUNT}/conversations/filter')]
        self.assertEqual(json.loads(filtered.content)['payload'][0]['attribute_key'], 'status')
        self.assertEqual(filtered.url.params['page'], '2')
        self.assertEqual(json.loads(by_path[('POST', f'{ACCOUNT}/contact_inboxes/filter')].content), {'inbox_id': 3, 'source_id': '+919800000000'})
        summary = by_path[('GET', '/api/v2/accounts/1/reports/summary')]
        self.assertEqual((summary.url.params['business_hours'], summary.url.params['type']), ('false', 'account'))

    def test_the_account_comes_from_the_setting_not_the_ai(self):
        async def scenario(session, client):
            return await session.call('list_labels', {})

        with patch.dict('os.environ', {'ORM_ACCOUNT_ID': '4'}):
            (is_error, _), api, _ = run(scenario, answers={('GET', '/api/v1/accounts/4/labels'): {'payload': []}})
        self.assertFalse(is_error)
        self.assertEqual(api.calls(), [('GET', '/api/v1/accounts/4/labels')])
        for junk in ('', 'x', '0', '-1', '2.5'):
            with patch.dict('os.environ', {'ORM_ACCOUNT_ID': junk}):
                self.assertEqual(backend.account_id(), 1)

    def test_a_portal_slug_is_one_escaped_segment(self):
        self.assertEqual(tools.build_path(SPECS['get_help_center_portal'], {'portal_id': 'help-center'}), f'{ACCOUNT}/portals/help-center')
        with self.assertRaises(Exception):
            tools.checked_arguments(SPECS['get_help_center_portal']['args'], {'portal_id': 'a/b'})
        self.assertEqual(tools.build_path(SPECS['get_conversation'], {'conversation_id': 41}), f'{ACCOUNT}/conversations/41')

    def test_a_post_read_is_only_a_lookup(self):
        for name, spec in SPECS.items():
            if spec['method'] == 'POST':
                self.assertTrue(spec['path'].startswith('/api/v'), name)
                self.assertTrue(any(a['in'] == 'body' for a in spec['args']), name)

    def test_the_html_of_an_email_is_left_out_unless_asked(self):
        message = {'id': 1, 'content': 'Hello', 'content_attributes': {'email': {'subject': 'Sofa', 'html_content': {'full': '<p>big</p>'}}}}
        answers = {('GET', f'{ACCOUNT}/conversations/41/messages'): lambda q, b: {'payload': [json.loads(json.dumps(message))]}}

        async def scenario(session, client):
            plain = await session.call('list_messages', {'conversation_id': 41})
            html = await session.call('list_messages', {'conversation_id': 41, 'include_html': True})
            return json.loads(plain[1]), json.loads(html[1])

        (plain, html), api, _ = run(scenario, answers=answers)
        self.assertEqual(plain['payload'][0]['content_attributes']['email'], {'subject': 'Sofa'})
        self.assertEqual(html['payload'][0]['content_attributes']['email']['html_content'], {'full': '<p>big</p>'})
        self.assertNotIn('include_html', {k for r in api.requests for k in r.url.params})

    def test_an_answer_is_cut_to_fit_with_a_note(self):
        rows = [{'id': i, 'content': 'word ' * 60} for i in range(1000)]

        async def scenario(session, client):
            return (await session.call('list_messages', {'conversation_id': 41}))[1]

        text, _, _ = run(scenario, answers={('GET', f'{ACCOUNT}/conversations/41/messages'): {'payload': rows}})
        self.assertLessEqual(len(text), tools.MAX_CHARS + 500)
        cut = json.loads(text)
        self.assertEqual(cut['note'], tools.CUT_NOTE)
        self.assertEqual(set(cut['payload'][-1]), {'more_not_shown'})
        self.assertEqual(len(cut['payload']) - 1 + cut['payload'][-1]['more_not_shown'], 1000)

    def test_a_small_answer_is_returned_as_it_is(self):
        self.assertEqual(tools.fit({'a': [1, 2, 3]}), {'a': [1, 2, 3]})


class BackendTests(unittest.TestCase):
    def test_query_pairs_the_way_rails_reads_them(self):
        pairs = backend.query_pairs({'labels': ['a', 'b'], 'page': 2, 'flag': True, 'off': False, 'empty': '', 'none': None, 'q': 'x y'})
        self.assertEqual(pairs, [('labels[]', 'a'), ('labels[]', 'b'), ('page', 2), ('flag', 'true'), ('off', 'false'), ('q', 'x y')])

    def test_paths_that_leave_the_api_are_refused(self):
        for path in ('/api/v1/../../app/login', '/api/v1/accounts/1/%2e%2e/x', '/app/login', '/api/v3/x', 'api/v1/x', '/api/v1/', '/api/v1//x',
                     '/api/v1/x?y=1', '/api/v1/x#y', '/api/v1/a%2F..%2Fb', 'http://elsewhere.test/api/v1/x', '/api/v1/a\\..\\b', '/platform/api/v1/x'):
            with self.assertRaises(Exception, msg=path):
                backend.checked_path(path)
        self.assertEqual(backend.checked_path('/api/v2/accounts/1/reports/summary'), '/api/v2/accounts/1/reports/summary')

    def test_a_redirect_is_not_followed(self):
        async def scenario(session, client):
            return await session.call('list_labels', {})

        # The fake answers 301 to a call that does not say https; this server always does, so force it to be missed.
        def without_proto(request):
            return httpx.Response(301, headers={'location': 'https://orm.test/x'})

        from mcp_server.tests.support import FakeRails
        fake = FakeRails()
        fake.transport = httpx.MockTransport(without_proto)
        from mcp_server.tests.support import Passes, serve, sidecar
        minted = Passes()
        with sidecar(minted, fake) as app:
            is_error, text = serve(app, minted, scenario)
        self.assertEqual((is_error, text), (True, 'ORM answered with a redirect, which this connection never follows.'))

    def test_a_second_use_of_the_pass_in_one_request_is_refused_before_it_is_sent(self):
        from types import SimpleNamespace
        from fastmcp.exceptions import ToolError
        request = SimpleNamespace(scope={})
        with patch.object(backend, 'get_http_request', return_value=request):
            backend._spend()
            with self.assertRaises(ToolError) as caught:
                backend._spend()
        self.assertEqual(str(caught.exception), backend.PASS_SPENT)
