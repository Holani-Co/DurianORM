# Tiny SQLite store for UI-editable OVERRIDE layers. Stdlib only — mirrors
# crm_state.py / reviews_state.py (no new deps, no service).
#
# Two independent config domains share this store, each with its own active
# version + history:
#   - "routing" : committed routing_rules.yaml stays the FLOOR; the UI publishes
#                 overrides; classifier.get_routing_rules() deep-merges them.
#   - "stores"  : data/store_registry.json stays the FLOOR; the UI publishes
#                 per-store overrides; store_locator merges them.
# An absent, empty, or broken override => the FLOOR wins, so a bad edit can never
# crash the store or routing path. Every publish is a new version → one-click
# rollback; domains never touch each other's active row.
#
# Tables:
#   config_versions(id, domain, doc_json, note, created_by, created_at, active)
#   config_audit(id, version_id, actor, action, diff_json, created_at)

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

_DB_PATH = os.environ.get(
    "CONFIG_STORE_DB", os.path.join(os.path.dirname(__file__), "config_store.db")
)
_lock = threading.Lock()

ROUTING = "routing"
STORES = "stores"


@contextmanager
def _conn():
    # sqlite3's own `with connection:` commits/rolls back but does NOT close the
    # connection — `with _conn() as c:` used to leak a file descriptor per call.
    # This wrapper closes it while keeping the same transaction behaviour.
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def init() -> None:
    """Create tables if absent and migrate older DBs. Safe on every startup."""
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS config_versions (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                domain     TEXT    NOT NULL DEFAULT 'routing',
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
        # Migrate a pre-domain DB: existing rows are the routing overrides.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(config_versions)")}
        if "domain" not in cols:
            c.execute("ALTER TABLE config_versions "
                      "ADD COLUMN domain TEXT NOT NULL DEFAULT 'routing'")


def get_active_override(domain: str = ROUTING) -> dict:
    """The live override document for `domain` (the caller merges it onto the
    FLOOR). Returns {} when there is no active version or it can't be parsed — so
    the caller safely falls back to the FLOOR. Never raises."""
    try:
        with _lock, _conn() as c:
            row = c.execute(
                "SELECT doc_json FROM config_versions "
                "WHERE active = 1 AND domain = ? ORDER BY id DESC LIMIT 1",
                (domain,)
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


def active_version(domain: str = ROUTING):
    """Metadata (no doc) of the live version for `domain`, or None."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id, note, created_by, created_at FROM config_versions "
            "WHERE active = 1 AND domain = ? ORDER BY id DESC LIMIT 1", (domain,)
        ).fetchone()
    return dict(row) if row else None


def list_versions(domain: str = ROUTING, limit: int = 50) -> list:
    """Recent versions for `domain` (newest first), metadata only."""
    with _lock, _conn() as c:
        rows = c.execute(
            "SELECT id, note, created_by, created_at, active FROM config_versions "
            "WHERE domain = ? ORDER BY id DESC LIMIT ?", (domain, int(limit))
        ).fetchall()
    return [dict(r) for r in rows]


def get_version(version_id: int):
    """Full row (incl. doc_json + domain) for one version, or None."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id, domain, doc_json, note, created_by, created_at, active "
            "FROM config_versions WHERE id = ?", (int(version_id),)
        ).fetchone()
    return dict(row) if row else None


def publish(doc: dict, note: str = "", actor: str = "", diff=None,
            domain: str = ROUTING) -> int:
    """Save `doc` as the new ACTIVE override for `domain`; deactivate that
    domain's previous active version (other domains untouched). Returns the new
    version id and writes an audit row."""
    doc_json = json.dumps(doc or {}, ensure_ascii=False, sort_keys=True)
    now = _now()
    with _lock, _conn() as c:
        c.execute("UPDATE config_versions SET active = 0 "
                  "WHERE active = 1 AND domain = ?", (domain,))
        cur = c.execute(
            "INSERT INTO config_versions "
            "(domain, doc_json, note, created_by, created_at, active) "
            "VALUES (?, ?, ?, ?, ?, 1)", (domain, doc_json, note or "", actor or "", now)
        )
        vid = cur.lastrowid
        c.execute(
            "INSERT INTO config_audit (version_id, actor, action, diff_json, created_at) "
            "VALUES (?, ?, 'publish', ?, ?)",
            (vid, actor or "", json.dumps(diff or {}, ensure_ascii=False), now)
        )
    return int(vid)


def rollback(version_id: int, actor: str = "") -> bool:
    """Make an earlier version active again (within its own domain). False if it
    doesn't exist."""
    now = _now()
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT id, domain FROM config_versions WHERE id = ?", (int(version_id),)
        ).fetchone()
        if not row:
            return False
        c.execute("UPDATE config_versions SET active = 0 "
                  "WHERE active = 1 AND domain = ?", (row["domain"],))
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
