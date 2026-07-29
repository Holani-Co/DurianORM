# Tiny SQLite store for the routing-config OVERRIDE layer edited from the ORM UI.
# Stdlib only — mirrors crm_state.py / reviews_state.py (no new deps, no service).
#
# The committed routing_rules.yaml (+ .local / env-override) stays the DEFAULT
# FLOOR. The UI publishes override documents here; classifier.get_routing_rules()
# deep-merges the ACTIVE override on top of the YAML. An absent, empty, or broken
# override => the YAML wins, so a bad edit can never crash the routing path.
#
# Tables:
#   config_versions(id, doc_json, note, created_by, created_at, active)
#     — every publish is a new row; exactly one row has active=1 (the live
#       override). Full history is retained → one-click rollback.
#   config_audit(id, version_id, actor, action, diff_json, created_at)
#     — who changed what, when (publish / rollback).

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

_DB_PATH = os.environ.get(
    "CONFIG_STORE_DB", os.path.join(os.path.dirname(__file__), "config_store.db")
)
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init() -> None:
    """Create tables if absent. Safe to call on every bridge startup."""
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_versions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                doc_json   TEXT    NOT NULL,
                note       TEXT    NOT NULL DEFAULT '',
                created_by TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL,
                active     INTEGER NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_audit (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                version_id INTEGER,
                actor      TEXT    NOT NULL DEFAULT '',
                action     TEXT    NOT NULL,
                diff_json  TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL
            )
        """)


def get_active_override() -> dict:
    """The live override document (deep-merged onto the YAML by the caller).
    Returns {} when there is no active version or it can't be parsed — so the
    caller safely falls back to the YAML defaults. Never raises."""
    try:
        with _lock, _conn() as c:
            row = c.execute(
                "SELECT doc_json FROM config_versions WHERE active = 1 "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    try:
        doc = json.loads(row["doc_json"] or "{}")
        return doc if isinstance(doc, dict) else {}
    except Exception:
        return {}


def active_version():
    """Metadata (no doc) of the live version, or None."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id, note, created_by, created_at FROM config_versions "
            "WHERE active = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def list_versions(limit: int = 50) -> list:
    """Recent versions (newest first), metadata only — for the History panel."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, note, created_by, created_at, active FROM config_versions "
            "ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(version_id: int):
    """Full row (incl. doc_json) for one version, or None."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id, doc_json, note, created_by, created_at, active "
            "FROM config_versions WHERE id = ?", (int(version_id),)
        ).fetchone()
    return dict(row) if row else None


def publish(doc: dict, note: str = "", actor: str = "", diff=None) -> int:
    """Save `doc` as a new ACTIVE override version; deactivate the previous one.
    Returns the new version id and writes an audit row."""
    doc_json = json.dumps(doc or {}, ensure_ascii=False, sort_keys=True)
    now = _now()
    with _lock, _conn() as c:
        c.execute("UPDATE config_versions SET active = 0 WHERE active = 1")
        cur = c.execute(
            "INSERT INTO config_versions (doc_json, note, created_by, created_at, active) "
            "VALUES (?, ?, ?, ?, 1)", (doc_json, note or "", actor or "", now)
        )
        vid = cur.lastrowid
        c.execute(
            "INSERT INTO config_audit (version_id, actor, action, diff_json, created_at) "
            "VALUES (?, ?, 'publish', ?, ?)",
            (vid, actor or "", json.dumps(diff or {}, ensure_ascii=False), now)
        )
    return int(vid)


def rollback(version_id: int, actor: str = "") -> bool:
    """Make an earlier version active again. False if it doesn't exist."""
    now = _now()
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id FROM config_versions WHERE id = ?", (int(version_id),)
        ).fetchone()
        if not row:
            return False
        c.execute("UPDATE config_versions SET active = 0 WHERE active = 1")
        c.execute("UPDATE config_versions SET active = 1 WHERE id = ?", (int(version_id),))
        c.execute(
            "INSERT INTO config_audit (version_id, actor, action, diff_json, created_at) "
            "VALUES (?, ?, 'rollback', '', ?)", (int(version_id), actor or "", now)
        )
    return True


def list_audit(limit: int = 100) -> list:
    """Recent audit entries (newest first)."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, version_id, actor, action, diff_json, created_at "
            "FROM config_audit ORDER BY id DESC LIMIT ?", (int(limit),)
        ).fetchall()
    return [dict(r) for r in rows]
