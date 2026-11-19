# CRM stage → WhatsApp template automation (Settings → CRM WhatsApp Automation).
#
# Zoho CRM calls the ORM when a Deal's Stage changes (a Zoho Workflow Rule
# webhook → Chatwoot's public /webhooks/crm_stage → this bridge). Each enabled
# RULE that matches the deal's new stage (and its field filters) sends an
# approved WhatsApp template to the deal's Mobile, from its product line's
# WhatsApp number, INSIDE the customer's ORM conversation — so it is logged and replies land in
# the ORM like any chat. A rule with a delay is scheduled instead and, when due,
# sent only if the deal is still in that stage (follow-ups stop once a lead
# moves on). Every rule fires at most once per deal.
#
# Rules are a versioned config_store document edited in the UI (rollback like
# the other Settings editors). DEFAULT_DOC pre-fills the rules, switched off.
#
# A PRODUCT LINE says who a set of rules is for: its filters pick the deals (FHC =
# the Home Studio layout) and its inbox is the number that speaks for it. Templates
# belong to one WhatsApp account, so a rule can only send what its own number has.
# A deal outside every line gets nothing, and each line has its own off switch.
#
# A rule can also fire when a Zoho LEAD is created (trigger "lead_created"; the
# same webhook, with lead_id instead of deal_id). The furniture number's nurture
# sequence (client sheet "Durian :: CURLs", tab 1) runs on deals: see _nurture.
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
import owner_sync
import store_locator
import zoho_crm

_DB_PATH = os.environ.get(
    "CRM_AUTOMATION_DB", os.path.join(os.path.dirname(__file__), "crm_automation.db"))
_lock = threading.Lock()

OPS = ("equals", "contains", "blank_or_contains", "not_empty", "not_contains")
# What starts a rule: a Deal reaching a stage, or a new Lead.
TRIGGERS = ("deal_stage", "lead_created")
# Where a template variable's value comes from. "field:<API_Name>" reads any
# Deal field; "text:<words>" is a fixed value.
SOURCES = {
    "contact_name": "Customer name (the deal's contact)",
    "first_name": "Customer first name",
    "owner_name": "Sales exec / store manager name (automatic from Stores and Zoho)",
    "owner_phone": "Sales exec / store manager phone (automatic from Stores and Zoho)",
    "deal_number": "Deal number",
    "deal_name": "Deal name",
    "stage": "Deal stage",
}

_WS_STAGE_QUALIFIED = "Qualified Deal (Please Select This )"
_WS_VARS_NAME = [{"source": "contact_name", "fallback": "there"}]
_WS_VARS_EXEC = _WS_VARS_NAME + [{"source": "owner_name", "fallback": "our team"}]
_WS_VARS_EXEC_PHONE = _WS_VARS_EXEC + [{"source": "owner_phone", "fallback": "this number"}]
# The house order for template variables: {{1}} customer, {{2}} sales exec /
# store manager, {{3}} their phone. Used to fill in variables a rule is missing.
STANDARD_VARIABLES = _WS_VARS_EXEC_PHONE
_NO_FILTER = [{"field": "", "op": "contains", "value": ""}]


def _rule(rid, line, name, stage, template, variables, delay_hours=0, *,
          trigger="deal_stage", delay_minutes=0, language="", match=None,
          still_in_stage=True, header_media_url="", max_age_days=0, once_per_phone_days=0):
    return {"id": rid, "line": line, "name": name, "enabled": False, "trigger": trigger,
            "stage": stage, "match": [dict(m) for m in (match or _NO_FILTER)],
            "template": template, "language": language,
            "delay_hours": delay_hours, "delay_minutes": delay_minutes,
            "only_if_still_in_stage": still_in_stage,
            "max_age_days": max_age_days, "once_per_phone_days": once_per_phone_days,
            "header_media_url": header_media_url, "variables": [dict(v) for v in variables]}


# Deals by vertical on the Standard layout: Furniture, plus blanks, because the
# Facebook lead forms (Durian Furniture Tier 1/2) leave Business Verticals empty.
# Doors, Franchise and Laminates deals carry their own value and stay out.
DEFAULT_LINES = [
    {"id": "fhc", "name": "Full Home Customisation", "enabled": True, "inbox_id": 25,
     "match": [{"field": "Layout", "op": "equals", "value": "Home Studio"}]},
    {"id": "furniture", "name": "Furniture", "enabled": False, "inbox_id": 29,
     "match": [{"field": "Layout", "op": "equals", "value": "Standard"},
               {"field": "Business_Verticals", "op": "blank_or_contains", "value": "Furniture"},
               {"field": "Business_Type_New", "op": "blank_or_contains", "value": "Retail"}]},
    # The same furniture number speaks for Doors and Franchise leads (tab 1).
    {"id": "doors", "name": "Doors", "enabled": False, "inbox_id": 29,
     "match": [{"field": "Layout", "op": "equals", "value": "Standard"},
               {"field": "Business_Verticals", "op": "contains", "value": "Doors"}]},
    {"id": "franchise", "name": "Franchise", "enabled": False, "inbox_id": 29,
     "match": [{"field": "Layout", "op": "equals", "value": "Standard"},
               {"field": "Business_Verticals", "op": "contains", "value": "Franchise"}]},
]
# The furniture number's lead nurture (client sheet "Durian :: CURLs", tab 1),
# on the templates already approved on that number. Every new Standard deal
# starts at Qualified Deal (a Zoho rule sets it on create), so the whole
# sequence hangs off that moment: 1 at once, 2 thirty minutes later, 3 and 4
# two and three days later — reaching website AND Facebook-form enquiries (the
# sheet's "lead created" would reach only the ~11 website leads a day; Zoho's own
# old M1–M4 rules also ran on deals). 2–4 still go if the deal moved on, never
# once it is Closed. Guards: a deal more than 3 days old (an old deal edited into
# Qualified) gets nothing, and one number gets each message at most once a week
# (Facebook forms often create several deals per person). Every fixed text is
# editable in Settings. Franchise gets message 1 only.
_COMPANY_PROFILE_PDF = ("https://view.publitas.com/29642/999116/pdfs/"
                        "7db48e0d-244e-4d78-b158-1a4f9b4aa28e.pdf")
_STILL_OPEN = [{"field": "Stage", "op": "not_contains", "value": "Closed"}]
_OUR_STORY = ("Watch the film to know more about us "
              "https://www.youtube.com/watch?v=yLOaDbgoZLA&feature=youtu.be")
_FACTORY = ("Witness our manufacturing capabilities "
            "https://www.youtube.com/watch?v=J1iiYauxLQs")


def _nurture(line, label, interest, we_make, designers):
    name = [{"source": "contact_name", "fallback": "there"}]
    text = lambda v: {"source": f"text:{v}", "fallback": v}
    common = dict(language="en", match=_STILL_OPEN, still_in_stage=False,
                  max_age_days=3, once_per_phone_days=7)
    return [
        _rule(f"{line}_msg1", line, f"{label} · Message 1 (new enquiry)", _WS_STAGE_QUALIFIED,
              "message_1_new", name + [text(interest)], **common),
        _rule(f"{line}_msg2", line, f"{label} · Message 2 (after 30 min)", _WS_STAGE_QUALIFIED,
              "message__2", name + [text("crafting experiences"), text(_OUR_STORY)],
              delay_minutes=30, **common),
        _rule(f"{line}_msg3", line, f"{label} · Message 3 (after 2 days)", _WS_STAGE_QUALIFIED,
              "message_3_updated", name + [text(we_make)], delay_hours=48,
              header_media_url=_COMPANY_PROFILE_PDF, **common),
        _rule(f"{line}_msg4", line, f"{label} · Message 4 (after 3 days)", _WS_STAGE_QUALIFIED,
              "message4", name + [text(designers), text(designers), text(_FACTORY)],
              delay_hours=72, **common),
    ]


FURNITURE_RULES = (_nurture("furniture", "Furniture", "Furniture", "furniture", "Furniture")
                   + _nurture("doors", "Doors", "Doors", "doors", "Door")
                   + _nurture("franchise", "Franchise", "the Durian franchise",
                              "furniture", "Furniture")[:1])
# Furniture rules of the first draft (fur_crm_* templates that were never
# created): replaced by the nurture rules while still switched off.
_LEGACY_FURNITURE_IDS = {"fur_qualified", "fur_followup_1", "fur_followup_2",
                         "fur_followup_3", "fur_interested", "fur_closed_lost",
                         "fur_closed_won"}
DEFAULT_DOC = {
    "enabled": False,
    "team": [],
    "lines": DEFAULT_LINES,
    "rules": [
        _rule("ws_qualified", "fhc", "Workspaces · Qualified", _WS_STAGE_QUALIFIED,
              "durian_ws_qualified", _WS_VARS_EXEC_PHONE),
        _rule("ws_followup_1", "fhc", "Workspaces · Follow-up 1", "Follow Up",
              "durian_ws_followup_1", _WS_VARS_EXEC_PHONE),
        _rule("ws_followup_2", "fhc", "Workspaces · Follow-up 2 (after 2 days)", "Follow Up",
              "durian_ws_followup_2", _WS_VARS_EXEC_PHONE, delay_hours=48),
        _rule("ws_followup_3", "fhc", "Workspaces · Follow-up 3 (after 5 days)", "Follow Up",
              "durian_ws_followup_3", _WS_VARS_EXEC_PHONE, delay_hours=120),
        _rule("ws_interested", "fhc", "Workspaces · Interested", "Interested",
              "durian_ws_interested", _WS_VARS_NAME),
        _rule("ws_closed_lost", "fhc", "Workspaces · Closed Lost", "Closed Lost",
              "durian_ws_closed_lost", _WS_VARS_NAME),
        _rule("ws_closed_won", "fhc", "Workspaces · Closed Won", "Closed Won",
              "durian_ws_closed_won", _WS_VARS_NAME),
    ] + FURNITURE_RULES,
}


def with_lines(doc: dict) -> dict:
    """`doc` with product lines. A document published before lines existed sent
    every rule from one inbox: those rules become FHC, on that inbox, with the
    Home Studio filter, and the furniture rules arrive switched off. A document
    from before the lead nurture gains the Doors / Franchise lines and the
    nurture rules (switched off), replacing the never-used fur_* rules."""
    if doc.get("lines"):
        return _with_nurture(doc)
    out = json.loads(json.dumps(doc))
    out["lines"] = json.loads(json.dumps(DEFAULT_LINES))
    out["lines"][0]["inbox_id"] = int(doc.get("inbox_id") or 25)
    out.pop("inbox_id", None)
    for rule in out.get("rules") or []:
        rule.setdefault("line", "fhc")
    out["rules"] = (out.get("rules") or []) + json.loads(json.dumps(FURNITURE_RULES))
    return out


def _with_nurture(doc: dict) -> dict:
    ids = {r.get("id") for r in doc.get("rules") or []}
    have_lines = {ln.get("id") for ln in doc.get("lines") or []}
    if "furniture_msg1" in ids and {"doors", "franchise"} <= have_lines:
        return doc
    out = json.loads(json.dumps(doc))
    out["lines"] += [json.loads(json.dumps(ln)) for ln in DEFAULT_LINES
                     if ln["id"] not in have_lines]
    if "furniture_msg1" not in ids:
        out["rules"] = [r for r in out["rules"]
                        if not (r.get("id") in _LEGACY_FURNITURE_IDS and not r.get("enabled"))]
        out["rules"] += json.loads(json.dumps(FURNITURE_RULES))
    return out


def get_doc() -> dict:
    """The published rules document, or the pre-filled default."""
    doc = config_store.get_active_override(config_store.CRM_AUTOMATION)
    return with_lines(doc) if isinstance(doc.get("rules"), list) \
        else json.loads(json.dumps(DEFAULT_DOC))


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
        cols = {r[1] for r in c.execute("PRAGMA table_info(runs)").fetchall()}
        if "snapshot" not in cols:      # the lead as it was, for a delayed send
            c.execute("ALTER TABLE runs ADD COLUMN snapshot TEXT NOT NULL DEFAULT ''")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _claim(deal_id: str, rule_id: str, stage: str, status: str, due_at: str,
           snapshot: str = "") -> bool:
    """Atomically take (record, rule). False = already sent / scheduled before."""
    now = _now().isoformat()
    with _lock, _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO runs (deal_id, rule_id, stage, status, due_at, "
            "created_at, updated_at, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (deal_id, rule_id, stage, status, due_at, now, now, snapshot))
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


def trigger_of(rule: dict) -> str:
    return rule.get("trigger") if rule.get("trigger") in TRIGGERS else "deal_stage"


def stage_matches(rule: dict, deal: dict) -> bool:
    """A deal rule: the deal is at the rule's stage. A lead rule has no stage."""
    if trigger_of(rule) == "lead_created":
        return True
    return bool(rule.get("stage")) and _norm(rule["stage"]) == _norm(deal.get("Stage"))


def delay_of(rule: dict) -> timedelta:
    return timedelta(hours=max(0, int(rule.get("delay_hours") or 0)),
                     minutes=max(0, int(rule.get("delay_minutes") or 0)))


def filters_match(rule: dict, deal: dict) -> tuple[bool, str]:
    """(matches, why-not). Empty filter rows are ignored."""
    for m in rule.get("match") or []:
        field, op, want = (m.get("field") or "").strip(), m.get("op") or "equals", m.get("value") or ""
        if not field:
            continue
        have = _value(deal, field)
        ok = (bool(have.strip()) if op == "not_empty"
              else _norm(want) in _norm(have) if op == "contains"
              else _norm(want) not in _norm(have) if op == "not_contains"
              else not have.strip() or _norm(want) in _norm(have) if op == "blank_or_contains"
              else _norm(have) == _norm(want))
        if not ok:
            return False, f"{field} is {have!r}, rule needs {op} {want!r}"
    return True, ""


def line_of(doc: dict, rule: dict) -> dict | None:
    return next((ln for ln in doc.get("lines") or [] if ln.get("id") == rule.get("line")), None)


def rule_applies(doc: dict, rule: dict, deal: dict) -> tuple[bool, str]:
    """(applies, why-not): the rule's product line is on and takes this deal, and
    the rule's own filters match."""
    line = line_of(doc, rule)
    if not line:
        return False, "the rule has no product line"
    if not line.get("enabled"):
        return False, f"{line.get('name') or line.get('id')} is switched off"
    ok, why = filters_match(line, deal)
    return filters_match(rule, deal) if ok else (False, why)


def _team_entry(deal: dict, team: list) -> dict:
    """The Sales team row for the deal's owner (matched by Zoho name or email)."""
    owner = deal.get("Owner") or {}
    keys = {_norm(owner.get("name")), _norm(owner.get("email"))} - {""}
    return next((t for t in team or [] if _norm(t.get("owner")) in keys), {})


def exec_contact(deal: dict, team: list) -> dict:
    """The sales exec / store manager the customer is pointed to: {name, phone}.
    First found wins, per value: a Sales team row (manual override) → the store
    this owner runs in Settings → Stores (manager + store phone) → the owner's
    Zoho user (Manager Name / Manager Phone fields, else their own number) →
    the Zoho owner's name. A studio login such as "Durian Full Home
    Customisation Goregaon" is not a person, so its store's manager is shown."""
    owner = deal.get("Owner") or {}
    row = _team_entry(deal, team)
    store = store_locator.store_for_owner(owner.get("id"), owner.get("name") or "") or {}
    zoho = owner_sync.contact(owner.get("id"))
    pick = lambda *vals: next((str(v).strip() for v in vals if str(v or "").strip()), "")
    return {"name": pick(row.get("name"), store.get("manager"), zoho["manager_name"],
                         _value(deal, "Owner")),
            "phone": pick(row.get("phone"), store.get("phone"), zoho["manager_phone"],
                          zoho["phone"])}


def fit_variables(variables: list, count: int) -> list:
    """`variables` sized to a template's `count`: extras dropped, missing ones
    filled in the house order (customer, sales exec, phone)."""
    out = [dict(v) for v in (variables or [])[:count]]
    while len(out) < count:
        i = len(out)
        out.append(dict(STANDARD_VARIABLES[i]) if i < len(STANDARD_VARIABLES)
                   else {"source": "contact_name", "fallback": ""})
    return out


def _on_number(doc: dict, rule: dict, templates: list) -> list:
    """The templates this rule can use: only those on its line's WhatsApp number."""
    inbox_id = (line_of(doc, rule) or {}).get("inbox_id")
    return [t for t in templates or [] if t.get("inbox_id") in (None, inbox_id)]


def template_mismatches(doc: dict, templates: list) -> list[str]:
    """Switched-on rules whose variable count differs from their approved template."""
    out = []
    for r in (doc or {}).get("rules") or []:
        tpl = find_template(_on_number(doc, r, templates), r) if r.get("enabled") else None
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
    exec_ = exec_contact(deal, team) if src in ("owner_name", "owner_phone") else {}
    contact = (_value(deal, "Contact_Name") or _value(deal, "Full_Name")
               or " ".join(x for x in (_value(deal, "First_Name"), _value(deal, "Last_Name")) if x)
               or str(deal.get("Deal_Name") or "").split(" — ")[0])
    value = {
        "contact_name": contact,
        "first_name": contact.split()[0] if contact.split() else "",
        "owner_name": exec_.get("name"),
        "owner_phone": exec_.get("phone"),
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
            "category": t.get("category"), "body": body, "inbox_id": t.get("inbox_id"),
            "var_count": len(set(re.findall(r"\{\{\s*(\d+)\s*\}\}", body))),
            "header_format": str((header or {}).get("format") or "").upper()}


def find_template(templates: list, rule: dict) -> dict | None:
    lang = _norm(rule.get("language"))
    for t in templates or []:
        if t.get("name") == rule.get("template") and str(t.get("status")).upper() == "APPROVED" \
                and (not lang or _norm(t.get("language")) == lang):
            return t
    return None


async def doc_templates(doc: dict) -> list:
    """Templates on every line's number, each carrying the inbox it belongs to."""
    out = []
    for inbox_id in sorted({int(ln.get("inbox_id") or 0) for ln in doc.get("lines") or []} - {0}):
        out += [{**t, "inbox_id": inbox_id}
                for t in await chatwoot.list_whatsapp_templates(inbox_id)]
    return out


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


async def _fetch_lead(lead_id: str) -> dict:
    r = await zoho_crm._crm_request("GET", f"/Leads/{lead_id}")
    data = (r.get("data") or [{}])[0]
    if not data.get("id"):
        raise RuntimeError(f"lead {lead_id} not found in Zoho (converted or deleted)")
    return {**data, "_module": "Leads"}


# Lead fields kept with a delayed lead message: a lead converted to a deal
# before message 2 is due is gone from Leads, and the message still goes out.
_LEAD_SNAPSHOT_FIELDS = ("id", "Full_Name", "First_Name", "Last_Name", "Mobile", "Phone",
                         "Lead_Status", "Business_Verticals", "Layout", "Business_Type_New",
                         "Lead_Source", "Owner")
_LEAD_KEY = "lead:"


def _is_lead(record: dict) -> bool:
    return record.get("_module") == "Leads"


async def send_rule(rule: dict, deal: dict, doc: dict, *, to_phone: str = "",
                    test: bool = False) -> dict:
    """Send one rule's template for this deal. Returns {status, detail, phone,
    conv_id}; status = sent | skipped | failed. `to_phone` + test=True sends to
    a test number instead of the customer (consent not checked)."""
    inbox_id = int((line_of(doc, rule) or {}).get("inbox_id") or 0)
    phone = re.sub(r"\D", "", to_phone) if to_phone else customer_phone(deal)
    if not phone:
        return {"status": "skipped", "detail": "the deal has no valid mobile number", "phone": ""}
    if not test and _sent_to_phone_recently(rule.get("id"), phone,
                                            int(rule.get("once_per_phone_days") or 0)):
        return {"status": "skipped", "phone": phone,
                "detail": f"this number already got this message in the last "
                          f"{rule.get('once_per_phone_days')} days (another deal)"}
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
            inbox_id, phone, "" if test else resolve_variable({"source": "contact_name"}, deal, []))
        if not test and await chatwoot.latest_whatsapp_consent(_contact_id, inbox_id) == "OPTED_OUT":
            return {"status": "skipped", "phone": phone, "conv_id": conv_id,
                    "detail": "customer opted out (replied STOP)"}
        await chatwoot.send_template_message(conv_id, _render(info["body"], values), {
            "name": tpl.get("name"), "language": tpl.get("language"),
            "category": tpl.get("category"), "processed_params": processed})
        if _is_lead(deal):
            where = (f"new lead {deal.get('id')}"
                     + (f" ({_value(deal, 'Lead_Source')})" if _value(deal, "Lead_Source") else ""))
        else:
            where = (f"deal {_value(deal, config.ZOHO_CRM_DEAL_NUMBER_FIELD or 'Deal_No') or deal.get('id')} "
                     f"is at stage “{_value(deal, 'Stage')}”")
        await chatwoot.post_private_note(
            conv_id, f"📲 CRM automation{' TEST' if test else ''}: sent `{tpl.get('name')}` — "
                     f"{where} (rule “{rule.get('name') or rule.get('id')}”).")
    except Exception as e:
        return {"status": "failed", "phone": phone, "detail": str(e)[:300]}
    return {"status": "sent", "phone": phone, "conv_id": conv_id, "detail": ""}


NEW_DEAL_STAGE_WAIT_SECONDS = 15


def _too_old(rule: dict, record: dict) -> bool:
    """The record was created more than the rule's max_age_days ago — an old
    deal edited into the stage, not a new enquiry. 0 = no limit."""
    days = int(rule.get("max_age_days") or 0)
    created = str(record.get("Created_Time") or "")
    if not days or not created:
        return False
    try:
        ts = datetime.fromisoformat(created)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return _now() - ts > timedelta(days=days)


def _sent_to_phone_recently(rule_id: str, phone: str, days: int) -> bool:
    """This rule already reached this number within `days` (another deal for
    the same person)."""
    if not days or not phone:
        return False
    since = (_now() - timedelta(days=days)).isoformat()
    with _lock, _conn() as c:
        row = c.execute("SELECT 1 FROM runs WHERE rule_id = ? AND phone = ? AND status = 'sent' "
                        "AND updated_at >= ? LIMIT 1", (rule_id, phone, since)).fetchone()
    return row is not None


def _active_rules(doc: dict, trigger: str = "deal_stage") -> list:
    return [r for r in doc.get("rules") or []
            if r.get("enabled") and r.get("id") and trigger_of(r) == trigger]


async def handle_deal(deal_id: str) -> list[dict]:
    """A deal changed stage: send or schedule every matching rule (once each)."""
    doc = get_doc()
    if not doc.get("enabled"):
        return [{"status": "skipped", "detail": "automation is switched off"}]
    deal = await _fetch_deal(str(deal_id))
    if not _value(deal, "Stage").strip():
        # A brand-new deal: Zoho's own "Update Stage to Qualified Deal" rule sets
        # the stage a moment after the create that called us — read it again.
        await asyncio.sleep(NEW_DEAL_STAGE_WAIT_SECONDS)
        deal = await _fetch_deal(str(deal_id))
    return await _run_rules(doc, _active_rules(doc, "deal_stage"), deal, str(deal_id),
                            _value(deal, "Stage"))


async def handle_lead(lead_id: str) -> list[dict]:
    """A lead was created: send or schedule every matching lead rule (once each)."""
    doc = get_doc()
    if not doc.get("enabled"):
        return [{"status": "skipped", "detail": "automation is switched off"}]
    lead = await _fetch_lead(str(lead_id))
    snap = json.dumps({k: lead.get(k) for k in _LEAD_SNAPSHOT_FIELDS}, default=str)
    return await _run_rules(doc, _active_rules(doc, "lead_created"), lead,
                            _LEAD_KEY + str(lead_id), "lead created", snap)


async def _run_rules(doc: dict, rules: list, record: dict, key: str, stage: str,
                     snapshot: str = "") -> list[dict]:
    out = []
    deal, deal_id = record, key
    for rule in rules:
        if not stage_matches(rule, deal) or not rule_applies(doc, rule, deal)[0]:
            continue
        if _too_old(rule, deal):
            out.append({"rule": rule["id"], "status": "skipped",
                        "detail": f"created more than {rule.get('max_age_days')} days ago"})
            continue
        delay = delay_of(rule)
        due = (_now() + delay).isoformat()
        if not _claim(str(deal_id), rule["id"], stage, "scheduled" if delay else "sending", due,
                      snapshot if delay else ""):
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
            if str(job["deal_id"]).startswith(_LEAD_KEY):
                deal = await _lead_for_job(job)
            else:
                deal = await _fetch_deal(job["deal_id"])
        except Exception as e:
            _finish(job["deal_id"], job["rule_id"], "failed", f"could not read the record: {e}")
            continue
        if rule.get("only_if_still_in_stage", True) and not stage_matches(rule, deal):
            _finish(job["deal_id"], job["rule_id"], "cancelled",
                    f"deal moved to “{_value(deal, 'Stage')}” before this was due")
            continue
        applies, why = rule_applies(doc, rule, deal)
        if not applies:
            _finish(job["deal_id"], job["rule_id"], "cancelled", why)
            continue
        _finish(job["deal_id"], job["rule_id"], "sending")
        res = await send_rule(rule, deal, doc)
        _finish(job["deal_id"], job["rule_id"], res["status"], res.get("detail", ""),
                res.get("phone", ""), res.get("conv_id"))
    return len(jobs)


async def _lead_for_job(job: dict) -> dict:
    """The lead behind a delayed lead message: fresh from Zoho, or — once it was
    converted to a deal and left the Leads module — as it was when created."""
    lead_id = str(job["deal_id"])[len(_LEAD_KEY):]
    try:
        return await _fetch_lead(lead_id)
    except Exception:
        snap = json.loads(job.get("snapshot") or "{}")
        if not snap:
            raise
        return {**snap, "_module": "Leads"}


async def preview(deal_id: str) -> dict:
    """What each rule would do for this deal right now — nothing is sent."""
    doc = get_doc()
    deal = await _fetch_deal(str(deal_id))
    templates = await doc_templates(doc)
    rows = []
    for rule in doc.get("rules") or []:
        ok_f, why = rule_applies(doc, rule, deal)
        tpl = find_template(_on_number(doc, rule, templates), rule)
        lead_rule = trigger_of(rule) == "lead_created"
        rows.append({
            "rule": rule.get("id"), "name": rule.get("name"), "enabled": bool(rule.get("enabled")),
            "stage_matches": False if lead_rule else stage_matches(rule, deal),
            "filters_match": ok_f,
            "why_not": "runs when a new lead is created, not for deals" if lead_rule else why,
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
    doc = with_lines(doc)
    for ln in doc["lines"]:
        label = ln.get("name") or ln.get("id") or "A product line"
        try:
            int(ln.get("inbox_id") or 0) or errors.append(f"{label}: choose the WhatsApp number.")
        except (TypeError, ValueError):
            errors.append(f"{label}: the inbox must be a number.")
        if not any((m.get("field") or "").strip() for m in ln.get("match") or []):
            errors.append(f"{label}: needs a filter, or its rules would reach every deal.")
        for m in ln.get("match") or []:
            if m.get("op") not in OPS:
                errors.append(f"{label}: unknown filter {m.get('op')!r}.")
    seen = set()
    for i, r in enumerate(doc["rules"], 1):
        label = r.get("name") or f"rule {i}"
        if not r.get("id") or r["id"] in seen:
            errors.append(f"{label}: needs a unique id.")
        seen.add(r.get("id"))
        if not line_of(doc, r):
            errors.append(f"{label}: choose a product line.")
        if r.get("trigger", "deal_stage") not in TRIGGERS:
            errors.append(f"{label}: unknown trigger {r.get('trigger')!r}.")
        elif trigger_of(r) == "deal_stage" and not str(r.get("stage") or "").strip():
            errors.append(f"{label}: set the CRM stage.")
        if not str(r.get("template") or "").strip():
            errors.append(f"{label}: choose a template.")
        try:
            if not 0 <= int(r.get("delay_hours") or 0) <= 24 * 30:
                errors.append(f"{label}: the delay must be 0–720 hours.")
            if not 0 <= int(r.get("delay_minutes") or 0) <= 59:
                errors.append(f"{label}: the extra minutes must be 0–59.")
            if not 0 <= int(r.get("max_age_days") or 0) <= 365 \
                    or not 0 <= int(r.get("once_per_phone_days") or 0) <= 365:
                errors.append(f"{label}: day limits must be 0–365.")
        except (TypeError, ValueError):
            errors.append(f"{label}: the delay must be a number of hours.")
        for v in r.get("variables") or []:
            src = str(v.get("source") or "")
            if src not in SOURCES and not src.startswith(("field:", "text:")):
                errors.append(f"{label}: unknown variable source {src!r}.")
        for m in r.get("match") or []:
            if m.get("op") not in OPS:
                errors.append(f"{label}: unknown filter {m.get('op')!r}.")
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
