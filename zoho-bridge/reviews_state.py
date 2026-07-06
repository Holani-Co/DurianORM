# Tiny SQLite state store for the reviews poller. Stdlib only — no extra deps.
#
# Two jobs:
#   1. Dedup — remember which Google review_ids we've already ingested.
#   2. Reply-back mapping — map a Chatwoot conversation_id to the Google
#      review reply_path, so when an agent replies in Chatwoot we know which
#      review to post it to.

import os
import sqlite3
import threading

_DB_PATH = os.environ.get(
    "REVIEWS_STATE_DB", os.path.join(os.path.dirname(__file__), "reviews_state.db")
)
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS seen_reviews (
                review_id       TEXT PRIMARY KEY,
                conversation_id INTEGER,
                reply_path      TEXT,
                stars           INTEGER,
                replied         INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conv_map (
                conversation_id INTEGER PRIMARY KEY,
                reply_path      TEXT,
                review_id       TEXT
            )
        """)
        # Auto-migrate: `update_time` tracks the review's Google updateTime so
        # the poller can detect EDITS (same review_id, newer updateTime). Rows
        # that predate this column keep '' — treated as "baseline unknown", so
        # the first sweep silently records their updateTime instead of firing a
        # spurious edit for every already-seen review.
        cols = [r[1] for r in c.execute("PRAGMA table_info(seen_reviews)").fetchall()]
        if "update_time" not in cols:
            c.execute("ALTER TABLE seen_reviews ADD COLUMN update_time TEXT DEFAULT ''")


def is_seen(review_id: str) -> bool:
    with _lock, _conn() as c:
        return c.execute(
            "SELECT 1 FROM seen_reviews WHERE review_id = ?", (review_id,)
        ).fetchone() is not None


def seen_record(review_id: str) -> dict | None:
    """The stored row for a review_id (or None if never seen). Used by the
    poller to detect edits: compare the returned `update_time` to Google's."""
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT conversation_id, reply_path, stars, replied, update_time "
            "FROM seen_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        return dict(row) if row else None


def mark_seen(review_id: str, conversation_id: int, reply_path: str,
              stars: int, replied: bool = False, update_time: str = ""):
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO seen_reviews "
            "(review_id, conversation_id, reply_path, stars, replied, update_time) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (review_id, conversation_id, reply_path, stars, int(replied), update_time),
        )
        if conversation_id:
            c.execute(
                "INSERT OR REPLACE INTO conv_map (conversation_id, reply_path, review_id) "
                "VALUES (?, ?, ?)",
                (conversation_id, reply_path, review_id),
            )


def reply_path_for_conversation(conversation_id: int) -> str | None:
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT reply_path FROM conv_map WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row["reply_path"] if row else None


def mark_replied(review_id: str):
    with _lock, _conn() as c:
        c.execute("UPDATE seen_reviews SET replied = 1 WHERE review_id = ?", (review_id,))
