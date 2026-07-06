# Tiny SQLite state for CRM owner routing. Stdlib only.
#
# Only job today: persistent round-robin counters so a govt/bulk city with
# multiple owners (e.g. Mumbai → rajesh.m / prabhat.sharma / mitesh) rotates
# fairly across deals, surviving bridge restarts.

import os
import sqlite3
import threading

_DB_PATH = os.environ.get(
    "CRM_STATE_DB", os.path.join(os.path.dirname(__file__), "crm_state.db")
)
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS round_robin (
                key   TEXT PRIMARY KEY,
                idx   INTEGER NOT NULL DEFAULT 0
            )
        """)


def next_index(key: str, n: int) -> int:
    """Return the next round-robin index in [0, n) for `key` and advance it.
    n <= 1 → always 0 (no rotation needed)."""
    if n <= 1:
        return 0
    with _lock, _conn() as c:
        row = c.execute("SELECT idx FROM round_robin WHERE key = ?", (key,)).fetchone()
        idx = (row["idx"] if row else 0) % n
        c.execute(
            "INSERT INTO round_robin (key, idx) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET idx = ?",
            (key, (idx + 1) % n, (idx + 1) % n),
        )
        return idx
