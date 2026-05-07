import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = "linkedin_bot.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts_seen (
    post_id TEXT PRIMARY KEY,
    author TEXT,
    post_text TEXT,
    post_url TEXT,
    post_urn TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comments_generated (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT REFERENCES posts_seen(post_id),
    archetype TEXT,
    comment_text TEXT,
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comments_posted (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id TEXT REFERENCES posts_seen(post_id),
    archetype TEXT,
    comment_text TEXT,
    was_edited BOOLEAN DEFAULT FALSE,
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT DEFAULT 'posted'
);

CREATE TABLE IF NOT EXISTS posts_skipped (
    post_id TEXT PRIMARY KEY REFERENCES posts_seen(post_id),
    skipped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slack_messages (
    ts TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    post_id TEXT,
    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def has_seen_post(post_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM posts_seen WHERE post_id = ?", (post_id,)
        ).fetchone()
        return row is not None


def save_post(post_id: str, author: str, post_text: str, post_url: str, post_urn: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT OR IGNORE INTO posts_seen (post_id, author, post_text, post_url, post_urn)
               VALUES (?, ?, ?, ?, ?)""",
            (post_id, author, post_text, post_url, post_urn),
        )


def save_generated_comments(post_id: str, comments: dict):
    with get_conn() as conn:
        for archetype, text in comments.items():
            conn.execute(
                "INSERT INTO comments_generated (post_id, archetype, comment_text) VALUES (?, ?, ?)",
                (post_id, archetype, text),
            )


def save_posted_comment(
    post_id: str, archetype: str, comment_text: str, was_edited: bool = False
):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO comments_posted (post_id, archetype, comment_text, was_edited)
               VALUES (?, ?, ?, ?)""",
            (post_id, archetype, comment_text, was_edited),
        )


def save_skipped_post(post_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO posts_skipped (post_id) VALUES (?)", (post_id,)
        )


def count_posted_today() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM comments_posted WHERE DATE(posted_at) = ?", (today,)
        ).fetchone()
        return row[0]


def seconds_since_last_post() -> float:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT posted_at FROM comments_posted ORDER BY posted_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return float("inf")
        last = datetime.fromisoformat(row["posted_at"])
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - last).total_seconds()


def last_comment_on_author(author: str) -> datetime | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT cp.posted_at FROM comments_posted cp
               JOIN posts_seen ps ON cp.post_id = ps.post_id
               WHERE ps.author = ?
               ORDER BY cp.posted_at DESC LIMIT 1""",
            (author,),
        ).fetchone()
        if not row:
            return None
        ts = datetime.fromisoformat(row["posted_at"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts


def get_post(post_id: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM posts_seen WHERE post_id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None


def save_slack_message(ts: str, channel: str, post_id: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO slack_messages (ts, channel, post_id) VALUES (?, ?, ?)",
            (ts, channel, post_id),
        )


def get_pending_slack_messages() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, channel FROM slack_messages"
        ).fetchall()
        return [dict(r) for r in rows]


def remove_slack_message(ts: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM slack_messages WHERE ts = ?", (ts,))


def clear_slack_messages():
    with get_conn() as conn:
        conn.execute("DELETE FROM slack_messages")


def get_recent_history(limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT cp.id, ps.author, cp.archetype, cp.comment_text,
                      cp.was_edited, cp.posted_at, cp.status
               FROM comments_posted cp
               JOIN posts_seen ps ON cp.post_id = ps.post_id
               ORDER BY cp.posted_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict:
    with get_conn() as conn:
        total_posts = conn.execute("SELECT COUNT(*) FROM posts_seen").fetchone()[0]
        total_generated = conn.execute(
            "SELECT COUNT(DISTINCT post_id) FROM comments_generated"
        ).fetchone()[0]
        total_posted = conn.execute("SELECT COUNT(*) FROM comments_posted").fetchone()[0]
        total_skipped = conn.execute("SELECT COUNT(*) FROM posts_skipped").fetchone()[0]
        edited = conn.execute(
            "SELECT COUNT(*) FROM comments_posted WHERE was_edited = 1"
        ).fetchone()[0]
        posted_today = count_posted_today()
        return {
            "posts_fetched": total_posts,
            "posts_with_comments_generated": total_generated,
            "comments_posted": total_posted,
            "comments_edited_before_posting": edited,
            "posts_skipped": total_skipped,
            "posted_today": posted_today,
        }
