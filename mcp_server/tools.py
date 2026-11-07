"""ORM's tools: one tool for each read of the registry (tools.json), one call each, read only for now.

`tools.json` is generated from chatwoot/custom/config/mcp_registry.yml (`bundle exec rake durian_os:mcp_tools`); this
module builds from it, for each read: the tool's name, title and description, its JSON schema (from `args`), and the
one Rails call it makes (`method` and `path`, with `:account_id` filled from ORM_ACCOUNT_ID, never by the AI).
tests/test_registry.py keeps tools.json equal to the registry, so a screen action added in the registry is a tool
only when it is exported, and a tool never exists without its registry entry.

Arguments are checked before any call (docs/mcp/MCP.md, section 12): no unknown name; path ids positive integers (a
help center portal's slug `^[a-z0-9-]{1,100}$`, sent as one escaped segment); strings at most 500 characters unless
the registry says less; enums as listed; arrays at most 50 items; an object or a filter payload at most 32 KB.
Arrays go to Rails the way it reads them (`labels[]=a&labels[]=b`). Answers are cut to 40,000 characters.

No lookup by name: a tool that takes a conversation takes its number (list and search tools give it), because finding
one from a name would be a second call, which a one-time pass cannot make.
"""
import copy
import json
import re
from pathlib import Path
from urllib.parse import quote

from fastmcp.exceptions import ToolError
from fastmcp.tools import Tool, ToolResult
from mcp.types import TextContent, ToolAnnotations
from pydantic import Field

from mcp_server.backend import account_id, api

TOOLS_JSON = Path(__file__).with_name('tools.json')
#: The most characters an answer may take; Claude and other clients cut or refuse larger tool results.
MAX_CHARS = 40_000
CUT_NOTE = ('This answer was cut to fit: a list that ends with {"more_not_shown": n} had n more items, and text ending '
            '"[cut]" was longer. Ask for less (a page, or a narrower search) to see the rest.')
MAX_STRING = 500
MAX_ITEMS = 50
MAX_OBJECT = 32 * 1024
SLUG = re.compile(r'^[a-z0-9-]{1,100}$')
PLACEHOLDER = re.compile(r':([a-z_]+)')

#: Arguments only this server knows: not part of the registry, never sent to Rails.
LOCAL_ARGS = {
    'list_messages': [{'name': 'include_html', 'in': 'local', 'type': 'boolean',
                       'description': 'Keep the HTML of email messages (large, rarely needed); left out unless asked.'}],
}


# ---- answers that fit -------------------------------------------------------------------------------------------
def _dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=str)


def _biggest(holder):
    """(container, key) of the largest thing that can be shortened: a list of two or more items, or a long text."""
    best, stack = None, [holder]
    while stack:
        container = stack.pop()
        for key, item in (container.items() if isinstance(container, dict) else enumerate(container)):
            if isinstance(item, (dict, list)):
                stack.append(item)
            real = item[:-1] if isinstance(item, list) and item and isinstance(item[-1], dict) and set(item[-1]) == {'more_not_shown'} else item
            if (isinstance(item, list) and len(real) >= 2) or (isinstance(item, str) and len(item) > 2000):
                size = len(_dumps(item))
                if best is None or size > best[0]:
                    best = (size, container, key)
    return best and best[1:]


def fit(value, limit=MAX_CHARS):
    """The answer, cut to `limit` characters of JSON: the largest lists are halved, each ending with how many items
    were left out, and very long texts shortened, until it fits. A small answer is returned as it is."""
    if len(_dumps(value)) <= limit:
        return value
    holder = {'value': copy.deepcopy(value)}
    for _ in range(500):
        if len(_dumps(holder)) <= limit:
            break
        found = _biggest(holder)
        if not found:
            break
        container, key = found
        item = container[key]
        if isinstance(item, str):
            container[key] = item[:len(item) // 2] + ' [cut]'
            continue
        more = item[-1]['more_not_shown'] if isinstance(item[-1], dict) and set(item[-1]) == {'more_not_shown'} else 0
        items = item[:-1] if more else item
        keep = len(items) // 2
        container[key] = items[:keep] + [{'more_not_shown': more + len(items) - keep}]
    out = holder['value']
    return {**out, 'note': CUT_NOTE} if isinstance(out, dict) else {'items': out, 'note': CUT_NOTE}


def drop_email_html(answer):
    """list_messages without the HTML bodies of email messages (content_attributes.email.html_content)."""
    messages = answer.get('payload') if isinstance(answer, dict) else None
    for message in messages if isinstance(messages, list) else []:
        email = ((message or {}).get('content_attributes') or {}).get('email') if isinstance(message, dict) else None
        if isinstance(email, dict):
            email.pop('html_content', None)
    return answer


# ---- the schema and the check of arguments ----------------------------------------------------------------------
def _schema(arg):
    """The JSON schema of one argument."""
    kind = arg['type']
    out = {'type': kind}
    if arg.get('description'):
        out['description'] = arg['description']
    if kind == 'integer' and arg['in'] == 'path':
        out['minimum'] = 1
    if arg.get('minimum') is not None:
        out['minimum'] = arg['minimum']
    if kind == 'string':
        out['maxLength'] = arg.get('maxLength', 100 if arg['in'] == 'path' else MAX_STRING)
        if arg.get('pattern'):
            out['pattern'] = arg['pattern']
        elif arg['in'] == 'path':
            out['pattern'] = SLUG.pattern
    if arg.get('enum'):
        out['enum'] = arg['enum']
    if kind == 'array':
        out['items'] = {'type': arg.get('items') or 'string'}
        if out['items']['type'] == 'string':
            out['items']['maxLength'] = MAX_STRING
        out['maxItems'] = MAX_ITEMS
    return out


def parameters(args):
    """The tool's inputSchema: the registry's arguments (the ones the AI gives, not the account), plus local ones."""
    properties = {a['name']: _schema(a) for a in args}
    return {'type': 'object', 'properties': properties, 'required': [a['name'] for a in args if a.get('required')],
            'additionalProperties': False}


def _check(arg, value):
    """The argument's value if it is good, else a ToolError that names it."""
    name, kind = arg['name'], arg['type']
    where = f"Argument '{name}'"
    if kind == 'integer':
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolError(f'{where} must be a whole number.')
        floor = 1 if arg['in'] == 'path' else arg.get('minimum')
        if floor is not None and value < floor:
            raise ToolError(f'{where} must be at least {floor}.')
    elif kind == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ToolError(f'{where} must be a number.')
        if arg.get('minimum') is not None and value < arg['minimum']:
            raise ToolError(f"{where} must be at least {arg['minimum']}.")
    elif kind == 'boolean':
        if not isinstance(value, bool):
            raise ToolError(f'{where} must be true or false.')
    elif kind == 'string':
        if not isinstance(value, str):
            raise ToolError(f'{where} must be text.')
        value = value.strip()
        if len(value) > arg.get('maxLength', 100 if arg['in'] == 'path' else MAX_STRING):
            raise ToolError(f'{where} is too long.')
        pattern = arg.get('pattern') or (SLUG.pattern if arg['in'] == 'path' else None)
        if pattern and not re.match(pattern, value):
            raise ToolError(f'{where} is not in the form expected.')
    elif kind == 'array':
        if not isinstance(value, list) or len(value) > MAX_ITEMS:
            raise ToolError(f'{where} must be a list of at most {MAX_ITEMS} items.')
        inner = {'name': name, 'in': arg['in'], 'type': arg.get('items') or 'string'}
        value = [_check(inner, item) for item in value]
    elif kind == 'object':
        if not isinstance(value, dict) or len(_dumps(value)) > MAX_OBJECT:
            raise ToolError(f'{where} must be an object of at most 32 KB.')
    if arg.get('enum') and value not in arg['enum']:
        raise ToolError(f"{where} must be one of: {', '.join(str(v) for v in arg['enum'])}.")
    if arg['in'] == 'body' and kind == 'array' and len(_dumps(value)) > MAX_OBJECT:
        raise ToolError(f'{where} is larger than 32 KB.')
    return value


def checked_arguments(args, given):
    """{name: value} of the arguments, checked: unknown names, missing required ones and bad values are ToolErrors."""
    given = given or {}
    known = {a['name']: a for a in args}
    unknown = sorted(set(given) - set(known))
    if unknown:
        raise ToolError(f"Unknown argument: {', '.join(unknown)}. This tool takes: {', '.join(known) or 'no arguments'}.")
    out = {}
    for name, arg in known.items():
        value = given.get(name)
        if value is None or value == '':
            if arg.get('required'):
                raise ToolError(f"Give '{name}'.")
            continue
        out[name] = _check(arg, value)
    return out


# ---- the call -----------------------------------------------------------------------------------------------------
def build_path(spec, values):
    """The route with `:account_id` (the managed account) and each path id filled in, ids as one escaped segment."""
    path_args = {a.get('param', a['name']): a['name'] for a in spec['args'] if a['in'] == 'path'}

    def fill(match):
        key = match.group(1)
        if key == spec['account_param']:
            return str(account_id())
        return quote(str(values[path_args[key]]), safe='')

    return PLACEHOLDER.sub(fill, spec['path'])


class OrmTool(Tool):
    """One read of ORM's registry: checks its arguments, makes the one Rails call, returns ORM's answer cut to fit."""

    spec: dict = Field(exclude=True)

    async def run(self, arguments):
        spec = self.spec
        args = spec['args'] + LOCAL_ARGS.get(spec['name'], [])
        values = checked_arguments(args, arguments)
        remote = {a['name']: values[a['name']] for a in spec['args'] if a['name'] in values}
        path = build_path(spec, remote)
        query = {n: v for n, v in remote.items() if _arg_in(spec, n) == 'query'}
        body = {n: v for n, v in remote.items() if _arg_in(spec, n) == 'body'}
        answer = await api(spec['method'], path, params=query, json=body if spec['method'] != 'GET' else None)
        if spec['name'] == 'list_messages' and not values.get('include_html'):
            answer = drop_email_html(answer)
        return ToolResult(content=[TextContent(type='text', text=_dumps(fit(answer)))])


def _arg_in(spec, name):
    return next(a['in'] for a in spec['args'] if a['name'] == name)


def load(path=TOOLS_JSON):
    """The exported registry (tools.json): its version, counts and read tools."""
    with open(path, encoding='utf-8') as handle:
        document = json.load(handle)
    if document.get('version') != 1 or not isinstance(document.get('tools'), list):
        raise ValueError(f'{path}: version 1 with a tools list expected')
    return document


def make_tool(spec):
    args = spec['args'] + LOCAL_ARGS.get(spec['name'], [])
    return OrmTool(name=spec['name'], title=spec['title'], description=spec['description'], parameters=parameters(args),
                   spec=spec, annotations=ToolAnnotations(title=spec['title'], readOnlyHint=True, destructiveHint=False,
                                                          idempotentHint=True, openWorldHint=False))


def register(mcp, document=None):
    """Each read on the server under its registry name, title and description, labelled read-only."""
    document = document or load()
    for spec in document['tools']:
        if spec['method'] not in ('GET', 'POST') or (spec['method'] == 'POST' and not spec['path'].startswith('/api/v')):
            raise ValueError(f"{spec['name']}: not a read this server can make")
        mcp.add_tool(make_tool(spec))
    return document
