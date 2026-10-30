# CRM stage → WhatsApp template automation (Settings → CRM WhatsApp Automation).
#
# Zoho CRM calls the ORM when a Deal's Stage changes (a Zoho Workflow Rule
# webhook → Chatwoot's public /webhooks/crm_stage → this bridge). Each enabled
# RULE that matches the deal's new stage (and its field filters) sends an
# approved WhatsApp template to the deal's Mobile, from the configured inbox,
# INSIDE the customer's ORM conversation — so it is logged and replies land in
# the ORM like any chat. A rule with a delay is scheduled instead and, when due,
# sent only if the deal is still in that stage (follow-ups stop once a lead
# moves on). Every rule fires at most once per deal.
#
# Rules are a versioned config_store document edited in the UI (rollback like
# the other Settings editors). DEFAULT_DOC pre-fills the Durian Workspaces
# rules, switched off until the team sets which deals they apply to.
#
# Consent: the CRM lead form counts as consent; anyone who replied STOP
# (Chatwoot records OPTED_OUT) is never sent an automated message.

import asyncio
import json
import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import chatwoot
import config
import config_store
import zoho_crm

_DB_PATH = os.environ.get(
    "CRM_AUTOMATION_DB", os.path.join(os.path.dirname(__file__), "crm_automation.db"))
_lock = threading.Lock()

OPS = ("equals", "contains", "not_empty")
# Where a template variable's value comes from. "field:<API_Name>" reads any
# Deal field; "text:<words>" is a fixed value.
SOURCES = {
    "contact_name": "Customer name (the deal's contact)",
    "first_name": "Customer first name",
    "owner_name": "Sales exec name (Sales team \"Name to show\", else the Zoho owner)",
    "owner_phone": "Sales exec phone (from the Sales team list)",
    "deal_number": "Deal number",
    "deal_name": "Deal name",
    "stage": "Deal stage",
}

_WS_STAGE_QUALIFIED = "Qualified Deal (Please Select This )"
_WS_VARS_NAME = [{"source": "contact_name", "fallback": "there"}]
_WS_VARS_EXEC = _WS_VARS_NAME + [{"source": "owner_name", "fallback": "our workspace expert"}]
_WS_VARS_EXEC_PHONE = _WS_VARS_EXEC + [{"source": "owner_phone", "fallback": "this number"}]
# The house order for template variables: {{1}} customer, {{2}} sales exec /
# store manager, {{3}} their phone. Used to fill in variables a rule is missing.
STANDARD_VARIABLES = _WS_VARS_EXEC_PHONE
_WS_MATCH = [{"field": "", "op": "contains", "value": ""}]   # the team fills this in


def _ws_rule(rid, name, stage, template, variables, delay_hours=0):
    return {"id": rid, "name": name, "enabled": False, "stage": stage,
            "match": [dict(m) for m in _WS_MATCH], "template": template, "language": "",
            "delay_hours": delay_hours, "only_if_still_in_stage": True,
            "header_media_url": "", "variables": [dict(v) for v in variables]}


DEFAULT_DOC = {
    "enabled": False,
    "inbox_id": 25,          # Whatsapp FHC
    "team": [],
    "rules": [
        _ws_rule("ws_qualified", "Workspaces · Qualified", _WS_STAGE_QUALIFIED,
                 "durian_ws_qualified", _WS_VARS_EXEC_PHONE),
        _ws_rule("ws_followup_1", "Workspaces · Follow-up 1", "Follow Up",
                 "durian_ws_followup_1", _WS_VARS_EXEC_PHONE),
        _ws_rule("ws_followup_2", "Workspaces · Follow-up 2 (after 2 days)", "Follow Up",
                 "durian_ws_followup_2", _WS_VARS_EXEC_PHONE, delay_hours=48),
        _ws_rule("ws_followup_3", "Workspaces · Follow-up 3 (after 5 days)", "Follow Up",
                 "durian_ws_followup_3", _WS_VARS_EXEC_PHONE, delay_hours=120),
        _ws_rule("ws_interested", "Workspaces · Interested", "Interested",
                 "durian_ws_interested", _WS_VARS_NAME),
        _ws_rule("ws_closed_lost", "Workspaces · Closed Lost", "Closed Lost",
                 "durian_ws_closed_lost", _WS_VARS_NAME),
        _ws_rule("ws_closed_won", "Workspaces · Closed Won", "Closed Won",
                 "durian_ws_closed_won", _WS_VARS_NAME),
    ],
}


def get_doc() -> dict:
    """The published rules document, or the pre-filled default."""
    doc = config_store.get_active_override(config_store.CRM_AUTOMATION)
    return doc if isinstance(doc.get("rules"), list) else json.loads(json.dumps(DEFAULT_DOC))


# ── run log (sqlite): one row per (deal, rule) — the once-per-deal guarantee ──

@contextmanager
def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init() -> None:
    with _lock, _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            deal_id TEXT NOT NULL, rule_id TEXT NOT NULL, stage TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL, due_at TEXT NOT NULL DEFAULT '',
            detail TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
            conv_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            UNIQUE(deal_id, rule_id))""")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim(deal_id: str, rule_id: str, stage: str, status: str, due_at: str) -> bool:
    """Atomically take (deal, rule). False = already sent / scheduled before."""
    now = _now().isoformat()
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO runs (deal_id, rule_id, stage, status, due_at, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (deal_id, rule_id, stage, status, due_at, now, now))
        return cur.rowcount == 1


def _finish(deal_id: str, rule_id: str, status: str, detail: str = "",
            phone: str = "", conv_id=None) -> None:
    with _lock, _conn() as c:
        c.execute("UPDATE runs SET status = ?, detail = ?, phone = ?, conv_id = ?, "
                  "updated_at = ? WHERE deal_id = ? AND rule_id = ?",
                  (status, detail[:500], phone, conv_id, _now().isoformat(), deal_id, rule_id))


def recent_runs(limit: int = 100) -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM runs ORDER BY updated_at DESC LIMIT ?",
                         (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def _due_jobs() -> list[dict]:
    with _lock, _conn() as c:
        rows = c.execute("SELECT * FROM runs WHERE status = 'scheduled' AND due_at <= ?",
                         (_now().isoformat(),)).fetchall()
    return [dict(r) for r in rows]


# ── matching + variables ──────────────────────────────────────────────────────

def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().casefold()


def _value(deal: dict, field: str) -> str:
    v = deal.get(field)
    if isinstance(v, dict):
        v = v.get("name") or v.get("display_value") or v.get("id")
    elif isinstance(v, list):
        v = ", ".join(str((x or {}).get("name") if isinstance(x, dict) else x) for x in v)
    return "" if v is None else str(v)


def stage_matches(rule: dict, deal: dict) -> bool:
    return bool(rule.get("stage")) and _norm(rule["stage"]) == _norm(deal.get("Stage"))


def filters_match(rule: dict, deal: dict) -> tuple[bool, str]:
    """(matches, why-not). Empty filter rows are ignored."""
    for m in rule.get("match") or []:
        field, op, want = (m.get("field") or "").strip(), m.get("op") or "equals", m.get("value") or ""
        if not field:
            continue
        have = _value(deal, field)
        ok = (bool(have.strip()) if op == "not_empty"
              else _norm(want) in _norm(have) if op == "contains"
              else _norm(have) == _norm(want))
        if not ok:
            return False, f"{field} is {have!r}, rule needs {op} {want!r}"
    return True, ""


def _team_entry(deal: dict, team: list) -> dict:
    """The Sales team row for the deal's owner (matched by Zoho name or email)."""
    owner = deal.get("Owner") or {}
    keys = {_norm(owner.get("name")), _norm(owner.get("email"))} - {""}
    return next((t for t in team or [] if _norm(t.get("owner")) in keys), {})


def _owner_phone(deal: dict, team: list) -> str:
    return str(_team_entry(deal, team).get("phone") or "").strip()


def _owner_name(deal: dict, team: list) -> str:
    """The name customers see: the Sales team "Name to show" (a studio login
    such as "Durian Full Home Customisation Goregaon" is not a person), else
    the Zoho owner's name."""
    return str(_team_entry(deal, team).get("name") or "").strip() or _value(deal, "Owner")


def fit_variables(variables: list, count: int) -> list:
    """`variables` sized to a template's `count`: extras dropped, missing ones
    filled in the house order (customer, sales exec, phone)."""
    out = [dict(v) for v in (variables or [])[:count]]
    while len(out) < count:
        i = len(out)
        out.append(dict(STANDARD_VARIABLES[i]) if i < len(STANDARD_VARIABLES)
                   else {"source": "contact_name", "fallback": ""})
    return out


def template_mismatches(doc: dict, templates: list) -> list[str]:
    """Switched-on rules whose variable count differs from their approved template."""
    out = []
    for r in (doc or {}).get("rules") or []:
        tpl = find_template(templates, r) if r.get("enabled") else None
        if not tpl:
            continue
        want, have = template_summary(tpl)["var_count"], len(r.get("variables") or [])
        if want != have:
            out.append(f"{r.get('name') or r.get('id')}: template {r.get('template')} has "
                       f"{want} variables but the rule fills {have}. Pick the template "
                       f"again to size the list, then check each variable.")
    return out


def resolve_variable(var: dict, deal: dict, team: list) -> str:
    src = str(var.get("source") or "")
    contact = _value(deal, "Contact_Name") or str(deal.get("Deal_Name") or "").split(" — ")[0]
    value = {
        "contact_name": contact,
        "first_name": contact.split()[0] if contact.split() else "",
        "owner_name": _owner_name(deal, team),
        "owner_phone": _owner_phone(deal, team),
        "deal_number": _value(deal, config.ZOHO_CRM_DEAL_NUMBER_FIELD or "Deal_No"),
        "deal_name": _value(deal, "Deal_Name"),
        "stage": _value(deal, "Stage"),
    }.get(src)
    if value is None and src.startswith("field:"):
        value = _value(deal, src[6:].strip())
    if value is None and src.startswith("text:"):
        value = src[5:]
    return (value or "").strip() or str(var.get("fallback") or "").strip()


def customer_phone(deal: dict) -> str:
    """The deal's mobile as country-code digits (Indian 10-digit numbers get 91)."""
    raw = re.sub(r"\D", "", _value(deal, config.ZOHO_CRM_MOBILE_FIELD or "Mobile")
                 or _value(deal, "Phone"))
    raw = raw.lstrip("0")
    if len(raw) == 10 and raw[0] in "6789":
        return "91" + raw
    return raw if 11 <= len(raw) <= 15 else ""


# ── templates ─────────────────────────────────────────────────────────────────

def template_summary(t: dict) -> dict:
    comps = t.get("components") or []
    body = next((c.get("text") or "" for c in comps if str(c.get("type")).upper() == "BODY"), "")
    header = next((c for c in comps if str(c.get("type")).upper() == "HEADER"), None)
    return {"name": t.get("name"), "language": t.get("language"), "status": t.get("status"),
            "category": t.get("category"), "body": body,
            "var_count": len(set(re.findall(r"\{\{\s*(\d+)\s*\}\}", body))),
            "header_format": str((header or {}).get("format") or "").upper()}


def find_template(templates: list, rule: dict) -> dict | None:
    lang = _norm(rule.get("language"))
    for t in templates or []:
        if t.get("name") == rule.get("template") and str(t.get("status")).upper() == "APPROVED" \
                and (not lang or _norm(t.get("language")) == lang):
            return t
    return None


def _render(body: str, values: list) -> str:
    return re.sub(r"\{\{\s*(\d+)\s*\}\}",
                  lambda m: values[int(m.group(1)) - 1] if 0 < int(m.group(1)) <= len(values)
                  else m.group(0), body)


# ── the send ──────────────────────────────────────────────────────────────────

async def _fetch_deal(deal_id: str) -> dict:
    r = await zoho_crm._crm_request("GET", f"/Deals/{deal_id}")
    data = (r.get("data") or [{}])[0]
    if not data.get("id"):
        raise RuntimeError(f"deal {deal_id} not found in Zoho")
    return data


async def send_rule(rule: dict, deal: dict, doc: dict, *, to_phone: str = "",
                    test: bool = False) -> dict:
    """Send one rule's template for this deal. Returns {status, detail, phone,
    conv_id}; status = sent | skipped | failed. `to_phone` + test=True sends to
    a test number instead of the customer (consent not checked)."""
    inbox_id = int(doc.get("inbox_id") or 0)
    phone = re.sub(r"\D", "", to_phone) if to_phone else customer_phone(deal)
    if not phone:
        return {"status": "skipped", "detail": "the deal has no valid mobile number", "phone": ""}
    try:
        tpl = find_template(await chatwoot.list_whatsapp_templates(inbox_id), rule)
    except Exception as e:
        return {"status": "failed", "phone": phone, "detail": f"could not read templates: {e}"}
    if not tpl:
        return {"status": "skipped", "phone": phone,
                "detail": f"template {rule.get('template')!r} is not approved / synced in the inbox"}
    info = template_summary(tpl)
    values = [resolve_variable(v, deal, doc.get("team") or []) for v in rule.get("variables") or []]
    if len(values) != info["var_count"]:
        return {"status": "failed", "phone": phone,
                "detail": f"template has {info['var_count']} variables, rule fills {len(values)}"
                          " — open the rule in Settings and publish it again"}
    if any(not v for v in values):
        return {"status": "skipped", "phone": phone,
                "detail": "a variable has no value and no fallback"}
    processed = {"body": {str(i + 1): v for i, v in enumerate(values)}}
    if info["header_format"] in ("IMAGE", "VIDEO", "DOCUMENT"):
        url = str(rule.get("header_media_url") or "").strip()
        if not url:
            return {"status": "skipped", "phone": phone,
                    "detail": f"template has a {info['header_format'].lower()} header — set its media URL on the rule"}
        processed["header"] = {"media_url": url, "media_type": info["header_format"].lower()}
    try:
        _contact_id, conv_id = await chatwoot.ensure_whatsapp_conversation(
            inbox_id, phone, "" if test else _value(deal, "Contact_Name"))
        if not test and await chatwoot.latest_whatsapp_consent(_contact_id, inbox_id) == "OPTED_OUT":
            return {"status": "skipped", "phone": phone, "conv_id": conv_id,
                    "detail": "customer opted out (replied STOP)"}
        await chatwoot.send_template_message(conv_id, _render(info["body"], values), {
            "name": tpl.get("name"), "language": tpl.get("language"),
            "category": tpl.get("category"), "processed_params": processed})
        stage = _value(deal, "Stage")
        await chatwoot.post_private_note(
            conv_id, f"📲 CRM automation{' TEST' if test else ''}: sent `{tpl.get('name')}` — "
                     f"deal {_value(deal, config.ZOHO_CRM_DEAL_NUMBER_FIELD or 'Deal_No') or deal.get('id')} "
                     f"is at stage “{stage}” (rule “{rule.get('name') or rule.get('id')}”).")
    except Exception as e:
        return {"status": "failed", "phone": phone, "detail": str(e)[:300]}
    return {"status": "sent", "phone": phone, "conv_id": conv_id, "detail": ""}


def _active_rules(doc: dict) -> list:
    return [r for r in doc.get("rules") or [] if r.get("enabled") and r.get("id")]


async def handle_deal(deal_id: str) -> list[dict]:
    """A deal changed stage: send or schedule every matching rule (once each)."""
    doc = get_doc()
    if not doc.get("enabled"):
        return [{"status": "skipped", "detail": "automation is switched off"}]
    deal = await _fetch_deal(str(deal_id))
    out = []
    for rule in _active_rules(doc):
        if not stage_matches(rule, deal) or not filters_match(rule, deal)[0]:
            continue
        delay = max(0, int(rule.get("delay_hours") or 0))
        due = (_now() + timedelta(hours=delay)).isoformat()
        stage = _value(deal, "Stage")
        if not _claim(str(deal_id), rule["id"], stage, "scheduled" if delay else "sending", due):
            out.append({"rule": rule["id"], "status": "already handled"})
            continue
        if delay:
            out.append({"rule": rule["id"], "status": "scheduled", "due_at": due})
            continue
        res = await send_rule(rule, deal, doc)
        _finish(str(deal_id), rule["id"], res["status"], res.get("detail", ""),
                res.get("phone", ""), res.get("conv_id"))
        out.append({"rule": rule["id"], **res})
    return out


async def run_due() -> int:
    """Send scheduled follow-ups that are due — only if the deal is still where
    the rule expects it. Returns how many were processed."""
    jobs = _due_jobs()
    if not jobs:
        return 0
    doc = get_doc()
    rules = {r["id"]: r for r in doc.get("rules") or [] if r.get("id")}
    for job in jobs:
        rule = rules.get(job["rule_id"])
        if not doc.get("enabled") or not rule or not rule.get("enabled"):
            _finish(job["deal_id"], job["rule_id"], "cancelled", "rule or automation switched off")
            continue
        try:
            deal = await _fetch_deal(job["deal_id"])
        except Exception as e:
            _finish(job["deal_id"], job["rule_id"], "failed", f"could not read deal: {e}")
            continue
        if rule.get("only_if_still_in_stage", True) and not stage_matches(rule, deal):
            _finish(job["deal_id"], job["rule_id"], "cancelled",
                    f"deal moved to “{_value(deal, 'Stage')}” before this was due")
            continue
        _finish(job["deal_id"], job["rule_id"], "sending")
        res = await send_rule(rule, deal, doc)
        _finish(job["deal_id"], job["rule_id"], res["status"], res.get("detail", ""),
                res.get("phone", ""), res.get("conv_id"))
    return len(jobs)


async def preview(deal_id: str) -> dict:
    """What each rule would do for this deal right now — nothing is sent."""
    doc = get_doc()
    deal = await _fetch_deal(str(deal_id))
    templates = await chatwoot.list_whatsapp_templates(int(doc.get("inbox_id") or 0))
    rows = []
    for rule in doc.get("rules") or []:
        ok_f, why = filters_match(rule, deal)
        tpl = find_template(templates, rule)
        rows.append({
            "rule": rule.get("id"), "name": rule.get("name"), "enabled": bool(rule.get("enabled")),
            "stage_matches": stage_matches(rule, deal), "filters_match": ok_f, "why_not": why,
            "template_found": bool(tpl),
            "message": _render(template_summary(tpl)["body"],
                               [resolve_variable(v, deal, doc.get("team") or [])
                                for v in rule.get("variables") or []]) if tpl else ""})
    return {"deal": {"id": deal.get("id"), "name": _value(deal, "Deal_Name"),
                     "stage": _value(deal, "Stage"), "owner": _value(deal, "Owner"),
                     "phone": customer_phone(deal)},
            "automation_enabled": bool(doc.get("enabled")), "rules": rows}


def validate(doc) -> dict:
    """{ok, errors, warnings} for a rules document."""
    errors, warnings = [], []
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), list):
        return {"ok": False, "errors": ["The document must have a rules list."], "warnings": []}
    try:
        int(doc.get("inbox_id") or 0) or errors.append("Choose the WhatsApp inbox to send from.")
    except (TypeError, ValueError):
        errors.append("The inbox must be a number.")
    seen = set()
    for i, r in enumerate(doc["rules"], 1):
        label = r.get("name") or f"rule {i}"
        if not r.get("id") or r["id"] in seen:
            errors.append(f"{label}: needs a unique id.")
        seen.add(r.get("id"))
        if not str(r.get("stage") or "").strip():
            errors.append(f"{label}: set the CRM stage.")
        if not str(r.get("template") or "").strip():
            errors.append(f"{label}: choose a template.")
        try:
            if not 0 <= int(r.get("delay_hours") or 0) <= 24 * 30:
                errors.append(f"{label}: the delay must be 0–720 hours.")
        except (TypeError, ValueError):
            errors.append(f"{label}: the delay must be a number of hours.")
        for v in r.get("variables") or []:
            src = str(v.get("source") or "")
            if src not in SOURCES and not src.startswith(("field:", "text:")):
                errors.append(f"{label}: unknown variable source {src!r}.")
        for m in r.get("match") or []:
            if m.get("op") not in OPS:
                errors.append(f"{label}: unknown filter {m.get('op')!r}.")
        if r.get("enabled") and not any((m.get("field") or "").strip() for m in r.get("match") or []):
            warnings.append(f"{label}: no filter set — it will fire for EVERY deal at this stage.")
    for t in doc.get("team") or []:
        if not str(t.get("owner") or "").strip() and (t.get("phone") or t.get("name")):
            errors.append("Sales team: every row needs the deal owner's Zoho name or email.")
        if t.get("phone") and len(re.sub(r"\D", "", str(t["phone"]))) < 10:
            errors.append(f"Sales team: {t.get('owner') or '?'} has an invalid phone.")
    return {"ok": not errors, "errors": errors, "warnings": warnings}


async def run_forever():
    """Every minute: send the follow-ups that are due. Boot-safe."""
    init()
    while True:
        try:
            await run_due()
        except Exception as e:
            print(f"[crm-automation] due-run error: {e}")
        await asyncio.sleep(max(15, config.CRM_AUTOMATION_POLL_SECONDS))
