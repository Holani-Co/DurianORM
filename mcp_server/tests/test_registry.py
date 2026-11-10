"""tools.json is current: it is exactly what ORM's registry (chatwoot/custom/config/mcp_registry.yml) exports, so a read
added, changed or removed there is not forgotten here, and the server never offers a tool the registry does not list.
The Rails spec (spec/custom/config/mcp_registry_spec.rb) checks the same file against Ruby's own export; this checks it
without Rails, from the YAML alone, the way `rake durian_os:mcp_tools` builds it (DurianOs::Ai::Registry#export)."""
import asyncio
import re
import unittest
from pathlib import Path

import yaml

from mcp_server import server, tools
from mcp_server.tests.support import ALL_SPECS, EXPORT, READS, SPECS, WRITES

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / 'chatwoot' / 'custom' / 'config' / 'mcp_registry.yml'
ARG_KEYS = ('name', 'in', 'param', 'type', 'items', 'required', 'enum', 'minimum', 'maxLength', 'maxItems', 'pattern', 'description')


def exported(raw):
    """What the registry exports: the reads, in order, as DurianOs::Ai::Registry#export_tool builds them."""
    out = []
    for item in raw['read'] + [w for w in raw['write'] if 'refused' not in w]:
        method, path = item['route'].split(' ', 1)
        tool = {'name': item['name'], 'title': item['title'], 'description': item['description'], 'method': method, 'path': path,
                'who': item['who'], 'feature': item.get('feature'), 'account_param': item.get('account_param', 'account_id'),
                'args': [{k: v for k, v in arg.items() if k in ARG_KEYS} for arg in item.get('args') or []]}
        if 'refused' not in item and item in raw['write']:
            tool.update({'write': True, 'destructive': item.get('destructive') is True, 'idempotent': item.get('idempotent') is True,
                         'open_world': item.get('open_world') is True, 'fixed': item.get('fixed') or {}})
        out.append(tool)
    return out


class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(REGISTRY, encoding='utf-8') as handle:
            cls.raw = yaml.safe_load(handle)

    def test_tools_json_is_what_the_registry_exports(self):
        self.assertEqual(self.raw['version'], EXPORT['version'])
        want = exported(self.raw)
        self.assertEqual([t['name'] for t in EXPORT['tools']], [t['name'] for t in want],
                         'tools.json is out of date: run `bundle exec rake durian_os:mcp_tools` in chatwoot/ and commit it')
        for have, expected in zip(EXPORT['tools'], want):
            self.assertEqual(have, expected, f"{expected['name']} differs from the registry: run `bundle exec rake durian_os:mcp_tools`")

    def test_the_counts_say_what_the_registry_holds(self):
        counts = EXPORT['counts']
        refused = [w for w in self.raw['write'] if 'refused' in w]
        self.assertEqual(counts['read'], len(self.raw['read']))
        self.assertEqual(counts['write_offered'], len(self.raw['write']) - len(refused))
        self.assertEqual(counts['write_refused'], len(refused))
        self.assertEqual(counts['web_only'], sum(len(keys) for keys in self.raw['web_only'].values()))
        self.assertEqual(counts['read'] + counts['write_offered'], len(EXPORT['tools']))
        self.assertEqual(len(self.raw['write']), 191)
        self.assertEqual(len(refused), 165)

    def test_every_tool_stays_in_the_api(self):
        names = [t['name'] for t in EXPORT['tools']]
        self.assertEqual(len(names), len(set(names)))
        for spec in EXPORT['tools']:
            self.assertRegex(spec['name'], r'^[a-z][a-z0-9_]{2,63}$')
            self.assertIn(spec['method'], ('POST', 'PATCH', 'DELETE') if spec.get('write') else ('GET', 'POST'))
            self.assertRegex(spec['path'], r'^/api/v[12]/')
            self.assertIn(spec['who'], ('everyone', 'admin', 'reports', 'kb'))
            self.assertTrue(spec['title'] and spec['description'])
            self.assertEqual(len({a['name'] for a in spec['args']}), len(spec['args']), spec['name'])
            placeholders = set(re.findall(r':([a-z_]+)', spec['path']))
            account = {spec['account_param']} if spec['account_param'] != 'none' else set()
            filled = {a.get('param', a['name']) for a in spec['args'] if a['in'] == 'path'}
            self.assertEqual(placeholders, account | filled, spec['name'])

    def test_the_server_registers_exactly_the_reads_and_the_offered_writes(self):
        registered = asyncio.run(server.mcp.list_tools())
        self.assertEqual(sorted(t.name for t in registered), sorted(READS + WRITES))
        self.assertEqual(len(registered), EXPORT['counts']['read'] + EXPORT['counts']['write_offered'])
        for tool in registered:
            self.assertEqual(tool.annotations.readOnlyHint, tool.name not in WRITES, tool.name)

    def test_the_instructions_name_only_tools_that_exist(self):
        mentioned = set(re.findall(r'\b((?:get|list|find|search|count|check|preview|filter)_[a-z_]+)\b', server.INSTRUCTIONS))
        self.assertTrue(mentioned)
        self.assertEqual(sorted(mentioned - set(ALL_SPECS)), [])
