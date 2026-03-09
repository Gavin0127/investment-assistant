"""SQLite data layer for Xueqiu posts with FTS5 full-text search."""

import contextlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

# I7: 雪球数据使用 CST (UTC+8)
_CST = timezone(timedelta(hours=8))


class XueqiuDB:
    """Xueqiu posts SQLite storage with FTS5 search."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextlib.contextmanager
    def _get_conn(self):
        """I1: Context manager 确保连接始终关闭。"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        with self._get_conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS posts (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    type TEXT NOT NULL DEFAULT '2',
                    is_column INTEGER NOT NULL DEFAULT 0,
                    title TEXT,
                    text TEXT NOT NULL,
                    description TEXT,
                    created_at INTEGER NOT NULL,
                    edited_at INTEGER,
                    target TEXT,
                    retweet_status_id INTEGER NOT NULL DEFAULT 0,
                    retweet_text TEXT,
                    retweet_description TEXT,
                    reply_count INTEGER NOT NULL DEFAULT 0,
                    like_count INTEGER NOT NULL DEFAULT 0,
                    retweet_count INTEGER NOT NULL DEFAULT 0,
                    view_count INTEGER NOT NULL DEFAULT 0,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
                );

                CREATE TABLE IF NOT EXISTS images (
                    post_id INTEGER NOT NULL,
                    original_url TEXT NOT NULL,
                    local_path TEXT,
                    seq INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (post_id, seq),
                    FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
                );

                CREATE TABLE IF NOT EXISTS xueqiu_users (
                    user_id INTEGER PRIMARY KEY,
                    nickname TEXT NOT NULL,
                    avatar_url TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
                    title, text, description,
                    content='posts',
                    content_rowid='id'
                );

                -- Triggers to keep FTS in sync
                CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
                    INSERT INTO posts_fts(rowid, title, text, description)
                    VALUES (new.id, new.title, new.text, new.description);
                END;

                CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
                    INSERT INTO posts_fts(posts_fts, rowid, title, text, description)
                    VALUES ('delete', old.id, old.title, old.text, old.description);
                END;

                CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
                    INSERT INTO posts_fts(posts_fts, rowid, title, text, description)
                    VALUES ('delete', old.id, old.title, old.text, old.description);
                    INSERT INTO posts_fts(rowid, title, text, description)
                    VALUES (new.id, new.title, new.text, new.description);
                END;
            """)
            # Migration: add retweet_description column for existing databases
            try:
                conn.execute("ALTER TABLE posts ADD COLUMN retweet_description TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

    def save_post(self, post: dict):
        """Upsert a post. Fields not present in dict get defaults."""
        with self._get_conn() as conn:
            conn.execute(
                """INSERT INTO posts
                    (id, user_id, type, is_column, title, text, description,
                     created_at, edited_at, target, retweet_status_id, retweet_text,
                     retweet_description,
                     reply_count, like_count, retweet_count, view_count)
                VALUES
                    (:id, :user_id, :type, :is_column, :title, :text, :description,
                     :created_at, :edited_at, :target, :retweet_status_id, :retweet_text,
                     :retweet_description,
                     :reply_count, :like_count, :retweet_count, :view_count)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    type=excluded.type,
                    is_column=excluded.is_column,
                    title=excluded.title,
                    text=excluded.text,
                    description=excluded.description,
                    created_at=excluded.created_at,
                    edited_at=excluded.edited_at,
                    target=excluded.target,
                    retweet_status_id=excluded.retweet_status_id,
                    retweet_text=excluded.retweet_text,
                    retweet_description=excluded.retweet_description,
                    reply_count=excluded.reply_count,
                    like_count=excluded.like_count,
                    retweet_count=excluded.retweet_count,
                    view_count=excluded.view_count,
                    saved_at=strftime('%s', 'now') * 1000
                """,
                {
                    "id": post["id"],
                    "user_id": post.get("user_id", 0),
                    "type": post.get("type", "2"),
                    "is_column": int(bool(post.get("is_column", False))),
                    "title": post.get("title"),
                    "text": post["text"],
                    "description": post.get("description"),
                    "created_at": post["created_at"],
                    "edited_at": post.get("edited_at"),
                    "target": post.get("target"),
                    "retweet_status_id": post.get("retweet_status_id", 0),
                    "retweet_text": post.get("retweet_text"),
                    "retweet_description": post.get("retweet_description"),
                    "reply_count": post.get("reply_count", 0),
                    "like_count": post.get("like_count", 0),
                    "retweet_count": post.get("retweet_count", 0),
                    "view_count": post.get("view_count", 0),
                },
            )
            conn.commit()

    def get_post(self, post_id: int) -> Optional[dict]:
        """Get a single post by id."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
            if row is None:
                return None
            return dict(row)

    def save_image(self, post_id: int, original_url: str, local_path: str, seq: int):
        """Save an image record for a post."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO images (post_id, original_url, local_path, seq) "
                "VALUES (?, ?, ?, ?)",
                (post_id, original_url, local_path, seq),
            )
            conn.commit()

    def get_images(self, post_id: int) -> list[dict]:
        """Get all images for a post, ordered by seq."""
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM images WHERE post_id = ? ORDER BY seq", (post_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def list_posts(
        self,
        page: int = 1,
        per_page: int = 20,
        post_type: Optional[str] = None,
        query: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> tuple[list[dict], int]:
        """List posts with filtering, FTS search, and pagination.

        Args:
            page: Page number (1-based).
            per_page: Results per page.
            post_type: Filter — "original", "retweet", "column", or None for all.
            query: FTS5 search query.
            start_date: Filter posts on or after this date (YYYY-MM-DD).
            end_date: Filter posts on or before this date (YYYY-MM-DD).

        Returns:
            Tuple of (posts list, total count).
        """
        with self._get_conn() as conn:
            conditions = []
            params: list = []

            # C3: 搜索 — FTS5 不支持中文分词，改用 LIKE 模糊匹配
            if query:
                like_pattern = f"%{query}%"
                conditions.append(
                    "(title LIKE ? OR text LIKE ? OR description LIKE ?)"
                )
                params.extend([like_pattern, like_pattern, like_pattern])

            # I5: post_type 为 None 时不筛选
            if post_type == "original":
                conditions.append("retweet_status_id = 0")
                conditions.append("is_column = 0")
            elif post_type == "retweet":
                conditions.append("retweet_status_id != 0")
            elif post_type == "column":
                conditions.append("is_column = 1")

            # Date range
            if start_date:
                conditions.append("created_at >= ?")
                params.append(self._date_to_ms(start_date))
            if end_date:
                conditions.append("created_at <= ?")
                params.append(self._date_to_ms(end_date, end_of_day=True))

            if user_id is not None:
                conditions.append("user_id = ?")
                params.append(user_id)

            where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

            # Count
            total = conn.execute(f"SELECT COUNT(*) FROM posts{where}", params).fetchone()[0]

            # Fetch page
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM posts{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()

            return [dict(r) for r in rows], total

    def get_sync_state(self, key: str) -> Optional[str]:
        """Get a sync state value."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None

    def set_sync_state(self, key: str, value: str):
        """Set a sync state value (upsert)."""
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO sync_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=strftime('%s', 'now') * 1000",
                (key, value),
            )
            conn.commit()

    def get_sync_cursor(self, user_id: int) -> Optional[dict]:
        """Get per-user sync cursor. Returns None if never synced."""
        raw = self.get_sync_state(f"sync_cursor:{user_id}")
        if raw is None:
            return None
        return json.loads(raw)

    def set_sync_cursor(self, user_id: int, cursor: dict) -> None:
        """Save per-user sync cursor."""
        self.set_sync_state(f"sync_cursor:{user_id}", json.dumps(cursor))

    def count_posts(self, user_id: int) -> int:
        """Count posts for a specific user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0]

    def add_user(self, user_id: int, nickname: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO xueqiu_users (user_id, nickname) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET nickname=excluded.nickname",
                (user_id, nickname),
            )
            conn.commit()

    def remove_user(self, user_id: int) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM xueqiu_users WHERE user_id = ?", (user_id,))
            conn.commit()

    def list_users(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM xueqiu_users WHERE is_active = 1 ORDER BY created_at"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_user(self, user_id: int) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM xueqiu_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return dict(row) if row else None

    @staticmethod
    def _date_to_ms(date_str: str, end_of_day: bool = False) -> int:
        """Convert YYYY-MM-DD to millisecond timestamp (CST/UTC+8)."""
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=_CST)
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp() * 1000)
