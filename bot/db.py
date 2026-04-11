import sqlite3
import os
from datetime import datetime, date
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "logs" / "social_bot.db"


def get_conn():
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS replies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                platform    TEXT NOT NULL,          -- 'x' or 'reddit'
                post_url    TEXT NOT NULL,
                post_title  TEXT,
                post_snippet TEXT,
                reply_text  TEXT NOT NULL,
                product     TEXT,                   -- 'Solvea' | 'VOC.ai' | None
                status      TEXT DEFAULT 'posted',  -- 'posted' | 'failed' | 'skipped'
                error_msg   TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_date      TEXT NOT NULL,
                platform      TEXT NOT NULL,
                target        INTEGER,
                posted        INTEGER DEFAULT 0,
                failed        INTEGER DEFAULT 0,
                skipped       INTEGER DEFAULT 0,
                duration_secs INTEGER,
                created_at    TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_replies_platform_date
                ON replies(platform, created_at);
            CREATE INDEX IF NOT EXISTS idx_replies_post_url
                ON replies(post_url);

            CREATE TABLE IF NOT EXISTS leads (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                platform     TEXT NOT NULL,
                post_url     TEXT NOT NULL UNIQUE,
                post_title   TEXT,
                business_type TEXT,
                pain_points  TEXT,   -- JSON array
                lead_score   INTEGER,
                urgency      TEXT,
                reason       TEXT,
                replied      INTEGER DEFAULT 0,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS review_queue (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                post_url         TEXT NOT NULL,
                post_content     TEXT,
                suggested_comment TEXT NOT NULL,
                status           TEXT DEFAULT 'pending',   -- pending / approved / rejected
                created_at       TEXT DEFAULT (datetime('now')),
                actioned_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS follow_queue (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                handle      TEXT UNIQUE NOT NULL,
                source      TEXT DEFAULT 'manual',   -- manual / dr_discovered
                status      TEXT DEFAULT 'pending',  -- pending / followed / skipped
                followed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_follow_status ON follow_queue(status);
        """)


def log_reply(platform, post_url, post_title, post_snippet, reply_text,
              product=None, status="posted", error_msg=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO replies
              (platform, post_url, post_title, post_snippet, reply_text, product, status, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (platform, post_url, post_title, post_snippet, reply_text,
              product, status, error_msg))


def already_replied(post_url: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM replies WHERE post_url = ? AND status IN ('posted', 'failed')",
            (post_url,)
        ).fetchone()
        return row is not None


def get_today_count(platform: str) -> int:
    today = date.today().isoformat()
    with get_conn() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM replies
            WHERE platform = ? AND status = 'posted'
              AND date(created_at) = ?
        """, (platform, today)).fetchone()
        return row["cnt"] if row else 0


def get_stats(days=30):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                date(created_at) as day,
                platform,
                COUNT(*) FILTER (WHERE status='posted')  as posted,
                COUNT(*) FILTER (WHERE status='failed')  as failed,
                COUNT(*) FILTER (WHERE status='skipped') as skipped,
                COUNT(*) as total
            FROM replies
            WHERE date(created_at) >= date('now', ? || ' days')
            GROUP BY day, platform
            ORDER BY day DESC
        """, (f"-{days}",)).fetchall()
        return [dict(r) for r in rows]


def save_lead(lead: dict):
    import json as _json
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO leads
              (platform, post_url, post_title, business_type, pain_points, lead_score, urgency, reason, replied)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            lead.get("platform"),
            lead.get("post_url"),
            lead.get("post_title"),
            lead.get("business_type"),
            _json.dumps(lead.get("pain_points", [])),
            lead.get("lead_score"),
            lead.get("urgency"),
            lead.get("reason"),
            1,  # we replied
        ))


def get_leads(days=7):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM leads
            WHERE date(created_at) >= date('now', ? || ' days')
            ORDER BY lead_score DESC, created_at DESC
        """, (f"-{days}",)).fetchall()
        return [dict(r) for r in rows]


def get_recent_replies(limit=50):
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, platform, post_url, post_title, reply_text,
                   product, status, created_at
            FROM replies
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def add_to_review_queue(post_url: str, post_content: str, suggested_comment: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO review_queue (post_url, post_content, suggested_comment)
            VALUES (?, ?, ?)
        """, (post_url, post_content, suggested_comment))


def get_pending_reviews(limit=20) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, post_url, post_content, suggested_comment, created_at
            FROM review_queue
            WHERE status = 'pending'
            ORDER BY created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def update_review_status(review_id: int, status: str):
    """status: 'approved' or 'rejected'"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE review_queue
            SET status = ?, actioned_at = datetime('now')
            WHERE id = ?
        """, (status, review_id))


def import_follow_list(handles: list):
    """Bulk insert handles into follow_queue (INSERT OR IGNORE for dedup)."""
    with get_conn() as conn:
        conn.executemany("""
            INSERT OR IGNORE INTO follow_queue (handle, source)
            VALUES (?, 'manual')
        """, [(h,) for h in handles])


def get_pending_follows(limit=15) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, handle FROM follow_queue
            WHERE status = 'pending'
            ORDER BY id ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def mark_followed(handle: str):
    with get_conn() as conn:
        conn.execute("""
            UPDATE follow_queue
            SET status = 'followed', followed_at = datetime('now')
            WHERE handle = ?
        """, (handle,))


def already_in_follow_queue(handle: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM follow_queue WHERE handle = ?", (handle,)
        ).fetchone()
        return row is not None


def add_to_follow_queue(handle: str, source: str = 'dr_discovered'):
    """Add a single handle (INSERT OR IGNORE)."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO follow_queue (handle, source)
            VALUES (?, ?)
        """, (handle, source))


def get_follow_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) FILTER (WHERE status='pending')  as pending,
                COUNT(*) FILTER (WHERE status='followed') as followed,
                COUNT(*) FILTER (WHERE status='skipped')  as skipped
            FROM follow_queue
        """).fetchone()
        return dict(row) if row else {}
