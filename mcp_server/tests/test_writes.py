"""The write tools: offered only for what tools.json says, labelled not read-only (destructive where they cannot be
undone), refused here for any pass below Full access before a call is made, sent as the registry says (method, path,
JSON body with the pinned values), and never given a way to send a customer a reply."""
import json
import unittest

from mcp_server import tools
from mcp_server.tests.support import ACCOUNT, WRITE_SPECS, WRITES, me, run

READ_ONLY_WORDS = ('This connection is read-only, so it cannot change anything. Reconnect with Full access in Durian OS to make '
                   'changes.')


class OfferTests(unittest.TestCase):
    def test_writes_are_labelled_not_read_only_and_destructive_where_the_registry_says(self):
        for name, spec in WRITE_SPECS.items():
            hints = tools.make_tool(spec).annotations
            self.assertFalse(hints.readOnlyHint, name)
            self.assertEqual(hints.destructiveHint, bool(spec['destructive']), name)
        destructive = {n for n, s in WRITE_SPECS.items() if s['destructive']}
        self.assertTrue({'merge_contacts', 'delete_contact_note', 'delete_canned_response'} <= destructive)
        self.assertNotIn('run_macro', WRITE_SPECS)
        self.assertNotIn('mute_conversation', WRITE_SPECS)
        self.assertEqual(WRITE_SPECS['set_conversation_status']['args'][1]['enum'], ['open', 'pending', 'snoozed'])
        self.assertNotIn('add_private_note', destructive)

    def test_nothing_that_reaches_a_customer_or_an_outside_system_is_a_tool(self):
        for refused in ('send_message', 'start_conversation', 'send_offer_to_conversation', 'create_campaign', 'bulk_update_conversations',
                        'delete_conversation', 'delete_contact', 'create_zoho_ticket', 'draft_zoho_ticket', 'update_inbox_settings'):
            self.assertNotIn(refused, WRITES)

    def test_the_pinned_private_flag_is_not_an_argument_the_ai_can_change(self):
        spec = WRITE_SPECS['add_private_note']
        self.assertEqual(spec['fixed'], {'private': True})
        self.assertNotIn('private', tools.make_tool(spec).parameters['properties'])

    def test_a_write_has_a_body_in_json_and_no_query_arguments(self):
        for name, spec in WRITE_SPECS.items():
            self.assertTrue(all(a['in'] in ('path', 'body') for a in spec['args']), name)


class CallTests(unittest.TestCase):
    def test_a_full_connection_sends_the_write_as_the_registry_says(self):
        answers = {('POST', f'{ACCOUNT}/conversations/41/messages'): {'id': 900, 'private': True}}

        async def scenario(session, client):
            return await session.call('add_private_note', {'conversation_id': 41, 'content': 'Called the customer back.'})

        (is_error, text), api, _ = run(scenario, answers=answers)
        self.assertFalse(is_error, text)
        self.assertEqual(api.calls(), [('POST', f'{ACCOUNT}/conversations/41/messages')])
        self.assertEqual(json.loads(api.requests[0].content), {'content': 'Called the customer back.', 'private': True})

    def test_a_patch_and_a_delete_go_out_with_their_own_method(self):
        answers = {('PATCH', f'{ACCOUNT}/contacts/5'): {'id': 5}, ('DELETE', f'{ACCOUNT}/contacts/5/notes/9'): {}}

        async def scenario(session, client):
            return [await session.call('update_contact', {'contact_id': 5, 'name': 'Nina Lark'}),
                    await session.call('delete_contact_note', {'contact_id': 5, 'note_id': 9})]

        results, api, _ = run(scenario, answers=answers)
        self.assertEqual([e for e, _ in results], [False, False], results)
        self.assertEqual(api.calls(), [('PATCH', f'{ACCOUNT}/contacts/5'), ('DELETE', f'{ACCOUNT}/contacts/5/notes/9')])
        self.assertEqual(json.loads(api.requests[0].content), {'name': 'Nina Lark'})

    def test_a_pass_below_full_access_is_refused_here_with_no_call(self):
        async def scenario(session, client):
            out = []
            for level in ('view', 'edit', 'act', 'junk', None):
                out.append(await session.call('add_private_note', {'conversation_id': 41, 'content': 'Hi'}, max_level=level))
            return out

        results, api, _ = run(scenario)
        self.assertEqual(results, [(True, READ_ONLY_WORDS)] * 5)
        self.assertEqual(api.requests, [])

    def test_a_read_works_on_a_pass_below_full_access(self):
        async def scenario(session, client):
            return await session.call('list_labels', {}, max_level='view')

        (is_error, _), api, _ = run(scenario, answers={('GET', f'{ACCOUNT}/labels'): {'payload': []}})
        self.assertFalse(is_error)
        self.assertEqual(api.calls(), [('GET', f'{ACCOUNT}/labels')])

    def test_bad_arguments_are_refused_before_a_call(self):
        async def scenario(session, client):
            return [await session.call('add_private_note', {'conversation_id': 41, 'content': 'x', 'private': False}),
                    await session.call('add_private_note', {'conversation_id': 41}),
                    await session.call('run_macro', {'macro_id': 3, 'conversation_ids': [1]}),
                    await session.call('set_conversation_status', {'conversation_id': 41, 'status': 'resolved'}),
                    await session.call('set_conversation_status', {'conversation_id': 41, 'status': 'deleted'})]

        results, api, _ = run(scenario)
        self.assertTrue(all(is_error for is_error, _ in results), results)
        self.assertEqual(api.requests, [])

    def test_an_orm_refusal_comes_back_in_its_words(self):
        from mcp_server.tests.support import httpx
        answers = {('POST', f'{ACCOUNT}/conversations/41/unmute'): httpx.Response(
            403, json={'error': 'You are not authorized to do this action'})}

        async def scenario(session, client):
            return await session.call('unmute_conversation', {'conversation_id': 41})

        (is_error, text), _, _ = run(scenario, answers=answers)
        self.assertEqual((is_error, text), (True, 'Not allowed: You are not authorized to do this action'))


class ListTests(unittest.TestCase):
    def test_writes_are_listed_only_when_orm_names_them(self):
        async def scenario(session, client):
            return await session.tool_names()

        names, _, _ = run(scenario)  # ORM names the reads only (a lower pass)
        self.assertFalse(set(names) & set(WRITES))
        names, _, _ = run(scenario, answer=me(tools=['list_conversations', 'add_private_note', 'send_message']))
        self.assertEqual(sorted(names), ['add_private_note', 'list_conversations'])


if __name__ == '__main__':
    unittest.main()
