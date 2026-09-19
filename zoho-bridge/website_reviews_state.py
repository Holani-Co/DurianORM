# Tiny SQLite state store for the website-reviews poller. Stdlib only.
#
# Mirrors reviews_state.py (Google) but keyed by the website review id, and it
# stores the review id itself as the reply target (the reply-back API takes the
# id, not a Google reply_path). Two jobs:
#   1. Dedup — remember which website review ids we've already ingested.
#   2. Reply-back mapping — map a Chatwoot conversation_id → review id, so an
#      agent reply in Chatwoot posts back to the right review.

import os
import sqlite3
import threading
from contextlib import contextmanager

_DB_PATH = os.environ.get(
    "WEBSITE_REVIEWS_STATE_DB",
    os.path.join(os.path.dirname(__file__), "website_reviews_state.db"),
)
_lock = threading.Lock()


@contextmanager
def _conn():
    # sqlite3's own `with connection:` commits/rolls back the transaction but
    # does NOT close the connection — so `with _conn() as c:` used to leak one
    # file descriptor per call. This wrapper closes it, wrapping the caller's
    # work in a transaction just like before.
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()


def init():
    with _lock, _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS seen_reviews (
                review_id       TEXT PRIMARY KEY,
                conversation_id INTEGER,
                stars           INTEGER,
                replied         INTEGER DEFAULT 0,
                update_time     TEXT DEFAULT '',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS conv_map (
                conversation_id INTEGER PRIMARY KEY,
                review_id       TEXT
            )
        """)


def seen_record(review_id: str) -> dict | None:
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT conversation_id, stars, replied, update_time "
            "FROM seen_reviews WHERE review_id = ?", (review_id,)
        ).fetchone()
        return dict(row) if row else None


def mark_seen(review_id: str, conversation_id: int, stars: int,
              replied: bool = False, update_time: str = ""):
    with _lock, _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO seen_reviews "
            "(review_id, conversation_id, stars, replied, update_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (review_id, conversation_id, stars, int(replied), update_time),
        )
        if conversation_id:
            c.execute(
                "INSERT OR REPLACE INTO conv_map (conversation_id, review_id) "
                "VALUES (?, ?)",
                (conversation_id, review_id),
            )


def review_id_for_conversation(conversation_id: int) -> str | None:
    with _lock, _conn() as c:
        row = c.execute(
            "SELECT review_id FROM conv_map WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return row["review_id"] if row else None


def mark_replied(review_id: str):
    with _lock, _conn() as c:
        c.execute("UPDATE seen_reviews SET replied = 1 WHERE review_id = ?", (review_id,))
