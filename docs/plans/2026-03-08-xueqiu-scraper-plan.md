# 雪球内容爬取与展示 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 爬取雪球用户全部动态，存储到 SQLite，通过双栏页面浏览和搜索。

**Architecture:** Scrapling 爬虫（StealthyFetcher 登录 + Fetcher 爬取）→ SQLite（posts + images + FTS5）→ Flask API → Alpine.js 双栏页面。爬虫模块独立于现有投资研究流程，仅共享 Storage 的 base_dir。

**Tech Stack:** Scrapling, SQLite FTS5, Flask, Alpine.js, Tailwind CSS

**Design Doc:** `docs/plans/2026-03-08-xueqiu-scraper-design.md`

---

### Task 1: 添加 Scrapling 依赖

**Files:**
- Modify: `pyproject.toml:8-17`

**Step 1: 添加依赖**

在 `pyproject.toml` 的 `dependencies` 列表中添加 scrapling：

```toml
dependencies = [
    "openai>=1.40.0",
    "rich>=13.0.0",
    "prompt-toolkit>=3.0.0",
    "flask>=3.0.0",
    "tavily-python>=0.5.0",
    "requests>=2.31.0",
    "yfinance>=0.2.0",
    "akshare>=1.10.0",
    "scrapling[fetchers]>=0.3.0",
]
```

**Step 2: 安装依赖**

Run: `uv sync`
Expected: 成功安装 scrapling 及其依赖

**Step 3: 验证安装**

Run: `uv run python -c "from scrapling.fetchers import Fetcher, StealthyFetcher; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add scrapling dependency for xueqiu scraper"
```

---

### Task 2: XueqiuDB — SQLite 数据层

**Files:**
- Create: `core/xueqiu_db.py`
- Create: `tests/test_xueqiu_db.py`

**Step 1: Write the failing tests**

```python
# tests/test_xueqiu_db.py
"""Tests for XueqiuDB."""

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path):
    from core.xueqiu_db import XueqiuDB
    return XueqiuDB(str(tmp_path / "test.db"))


class TestSchema:
    def test_tables_created(self, db):
        conn = sqlite3.connect(db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "posts" in tables
        assert "images" in tables
        assert "sync_state" in tables
        assert "posts_fts" in tables
        conn.close()


class TestSaveAndGet:
    def test_save_and_get_post(self, db):
        post = {
            "id": 12345,
            "user_id": 1936609590,
            "type": "2",
            "is_column": False,
            "title": None,
            "text": "<p>测试内容</p>",
            "description": "测试内容",
            "created_at": 1709856000000,
            "edited_at": None,
            "target": "/1936609590/12345",
            "retweet_status_id": 0,
            "retweet_text": None,
            "reply_count": 5,
            "like_count": 10,
            "retweet_count": 3,
            "view_count": 100,
        }
        db.save_post(post)
        result = db.get_post(12345)
        assert result is not None
        assert result["id"] == 12345
        assert result["text"] == "<p>测试内容</p>"
        assert result["like_count"] == 10

    def test_save_post_upsert(self, db):
        post = {
            "id": 12345, "user_id": 1936609590, "type": "2",
            "text": "v1", "description": "v1", "created_at": 1709856000000,
        }
        db.save_post(post)
        post["text"] = "v2"
        post["like_count"] = 99
        db.save_post(post)
        result = db.get_post(12345)
        assert result["text"] == "v2"
        assert result["like_count"] == 99

    def test_save_and_get_image(self, db):
        post = {
            "id": 100, "user_id": 1, "type": "2",
            "text": "img", "description": "img", "created_at": 1000,
        }
        db.save_post(post)
        db.save_image(100, "https://cdn.example.com/a.jpg", "/local/a.jpg", 0)
        db.save_image(100, "https://cdn.example.com/b.jpg", "/local/b.jpg", 1)
        images = db.get_images(100)
        assert len(images) == 2
        assert images[0]["original_url"] == "https://cdn.example.com/a.jpg"
        assert images[1]["seq"] == 1


class TestListPosts:
    @pytest.fixture(autouse=True)
    def seed(self, db):
        for i in range(5):
            db.save_post({
                "id": i + 1, "user_id": 1, "type": "2" if i % 2 == 0 else "0",
                "is_column": i == 4,
                "title": f"Title {i}" if i == 4 else None,
                "text": f"Content {i}", "description": f"Desc {i}",
                "created_at": 1000 + i * 1000,
                "retweet_status_id": 100 if i == 3 else 0,
            })

    def test_list_all(self, db):
        posts, total = db.list_posts(page=1, per_page=10)
        assert total == 5
        assert len(posts) == 5
        # 按 created_at 倒序
        assert posts[0]["id"] == 5

    def test_list_pagination(self, db):
        posts, total = db.list_posts(page=1, per_page=2)
        assert total == 5
        assert len(posts) == 2

    def test_filter_original(self, db):
        posts, total = db.list_posts(post_type="original")
        # original = type != null AND retweet_status_id == 0
        assert all(p["retweet_status_id"] == 0 for p in posts)

    def test_filter_retweet(self, db):
        posts, total = db.list_posts(post_type="retweet")
        assert total == 1
        assert posts[0]["retweet_status_id"] == 100

    def test_filter_column(self, db):
        posts, total = db.list_posts(post_type="column")
        assert total == 1
        assert posts[0]["is_column"] == 1


class TestFTS:
    def test_search(self, db):
        db.save_post({
            "id": 1, "user_id": 1, "type": "2",
            "title": "铜价分析", "text": "铜价创新高的背后逻辑",
            "description": "铜价", "created_at": 1000,
        })
        db.save_post({
            "id": 2, "user_id": 1, "type": "0",
            "text": "今天天气不错", "description": "天气",
            "created_at": 2000,
        })
        posts, total = db.list_posts(query="铜价")
        assert total == 1
        assert posts[0]["id"] == 1


class TestSyncState:
    def test_get_set(self, db):
        assert db.get_sync_state("last_post_id") is None
        db.set_sync_state("last_post_id", "99999")
        assert db.get_sync_state("last_post_id") == "99999"
        db.set_sync_state("last_post_id", "100000")
        assert db.get_sync_state("last_post_id") == "100000"
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_xueqiu_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.xueqiu_db'`

**Step 3: Write the implementation**

```python
# core/xueqiu_db.py
"""雪球帖子 SQLite 存储"""

import os
import sqlite3
import time
from typing import Dict, List, Optional, Tuple

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS posts (
    id                INTEGER PRIMARY KEY,
    user_id           INTEGER NOT NULL,
    type              TEXT,
    is_column         BOOLEAN DEFAULT 0,
    title             TEXT,
    text              TEXT,
    description       TEXT,
    created_at        INTEGER NOT NULL,
    edited_at         INTEGER,
    target            TEXT,
    retweet_status_id INTEGER DEFAULT 0,
    retweet_text      TEXT,
    reply_count       INTEGER DEFAULT 0,
    like_count        INTEGER DEFAULT 0,
    retweet_count     INTEGER DEFAULT 0,
    view_count        INTEGER DEFAULT 0,
    fetched_at        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id),
    original_url  TEXT NOT NULL,
    local_path    TEXT,
    seq           INTEGER DEFAULT 0,
    UNIQUE(post_id, original_url)
);

CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
    title, text, description,
    content='posts', content_rowid='id'
);

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
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
"""

_UPSERT_POST_SQL = """
INSERT INTO posts (id, user_id, type, is_column, title, text, description,
                   created_at, edited_at, target, retweet_status_id, retweet_text,
                   reply_count, like_count, retweet_count, view_count, fetched_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    text=excluded.text, description=excluded.description,
    edited_at=excluded.edited_at, reply_count=excluded.reply_count,
    like_count=excluded.like_count, retweet_count=excluded.retweet_count,
    view_count=excluded.view_count, fetched_at=excluded.fetched_at;
"""


class XueqiuDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        with self._conn() as conn:
            conn.executescript(_SCHEMA_SQL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def save_post(self, post: Dict):
        now = int(time.time() * 1000)
        with self._conn() as conn:
            conn.execute(_UPSERT_POST_SQL, (
                post["id"], post.get("user_id", 0), post.get("type"),
                int(post.get("is_column", False)), post.get("title"),
                post.get("text"), post.get("description"),
                post.get("created_at", now), post.get("edited_at"),
                post.get("target"), post.get("retweet_status_id", 0),
                post.get("retweet_text"),
                post.get("reply_count", 0), post.get("like_count", 0),
                post.get("retweet_count", 0), post.get("view_count", 0),
                now,
            ))

    def get_post(self, post_id: int) -> Optional[Dict]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM posts WHERE id=?", (post_id,)).fetchone()
        return dict(row) if row else None

    def save_image(self, post_id: int, original_url: str,
                   local_path: Optional[str], seq: int = 0):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO images (post_id, original_url, local_path, seq) "
                "VALUES (?, ?, ?, ?)",
                (post_id, original_url, local_path, seq),
            )

    def get_images(self, post_id: int) -> List[Dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM images WHERE post_id=? ORDER BY seq", (post_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def list_posts(self, page: int = 1, per_page: int = 30,
                   post_type: str = "all", query: str = "",
                   start_date: str = "", end_date: str = "") -> Tuple[List[Dict], int]:
        conditions = []
        params: list = []

        if post_type == "original":
            conditions.append("retweet_status_id = 0")
        elif post_type == "retweet":
            conditions.append("retweet_status_id != 0")
        elif post_type == "column":
            conditions.append("is_column = 1")

        if start_date:
            # start_date is YYYY-MM-DD, created_at is ms timestamp
            conditions.append("created_at >= ?")
            params.append(self._date_to_ms(start_date))
        if end_date:
            conditions.append("created_at <= ?")
            params.append(self._date_to_ms(end_date, end_of_day=True))

        if query:
            # Use FTS5 to get matching rowids
            with self._conn() as conn:
                fts_rows = conn.execute(
                    "SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?",
                    (query,),
                ).fetchall()
            fts_ids = [r[0] for r in fts_rows]
            if not fts_ids:
                return [], 0
            placeholders = ",".join("?" * len(fts_ids))
            conditions.append(f"id IN ({placeholders})")
            params.extend(fts_ids)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        with self._conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM posts{where}", params).fetchone()[0]
            offset = (page - 1) * per_page
            rows = conn.execute(
                f"SELECT * FROM posts{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [per_page, offset],
            ).fetchall()

        return [dict(r) for r in rows], total

    def get_sync_state(self, key: str) -> Optional[str]:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM sync_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_sync_state(self, key: str, value: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sync_state (key, value) VALUES (?, ?)",
                (key, value),
            )

    def get_latest_post_id(self) -> Optional[int]:
        with self._conn() as conn:
            row = conn.execute("SELECT id FROM posts ORDER BY created_at DESC LIMIT 1").fetchone()
        return row[0] if row else None

    @staticmethod
    def _date_to_ms(date_str: str, end_of_day: bool = False) -> int:
        from datetime import datetime
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        if end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp() * 1000)
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_xueqiu_db.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add core/xueqiu_db.py tests/test_xueqiu_db.py
git commit -m "feat: add XueqiuDB SQLite data layer with FTS5 search"
```

---

### Task 3: XueqiuScraper — 爬虫核心

**Files:**
- Create: `core/xueqiu_scraper.py`
- Create: `tests/test_xueqiu_scraper.py`

**Step 1: Write the failing tests**

```python
# tests/test_xueqiu_scraper.py
"""Tests for XueqiuScraper (mocked network)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


@pytest.fixture()
def scraper(tmp_path):
    from core.xueqiu_scraper import XueqiuScraper
    db_path = str(tmp_path / "test.db")
    image_dir = str(tmp_path / "images")
    return XueqiuScraper(db_path, image_dir)


class TestParseTimelineResponse:
    def test_parse_posts(self, scraper):
        raw = {
            "statuses": [
                {
                    "id": 111,
                    "user_id": 1936609590,
                    "type": "2",
                    "is_column": False,
                    "title": "",
                    "text": "<p>Hello</p>",
                    "description": "Hello",
                    "created_at": 1709856000000,
                    "edited_at": 0,
                    "target": "/1936609590/111",
                    "retweet_status_id": 0,
                    "reply_count": 1,
                    "like_count": 2,
                    "retweet_count": 0,
                    "view_count": 50,
                },
            ]
        }
        posts = scraper._parse_timeline(raw)
        assert len(posts) == 1
        assert posts[0]["id"] == 111
        assert posts[0]["text"] == "<p>Hello</p>"

    def test_parse_empty(self, scraper):
        posts = scraper._parse_timeline({"statuses": []})
        assert posts == []

    def test_parse_retweet(self, scraper):
        raw = {
            "statuses": [
                {
                    "id": 222,
                    "user_id": 1936609590,
                    "type": None,
                    "text": "转发评论",
                    "description": "转发评论",
                    "created_at": 1709856000000,
                    "retweet_status_id": 999,
                    "retweeted_status": {
                        "text": "<p>原文内容</p>",
                    },
                },
            ]
        }
        posts = scraper._parse_timeline(raw)
        assert posts[0]["retweet_status_id"] == 999
        assert posts[0]["retweet_text"] == "<p>原文内容</p>"


class TestExtractImageUrls:
    def test_extract_from_html(self, scraper):
        html = '<p>文字<img src="https://xqimg.imedao.com/a.jpg">中间<img src="https://xqimg.imedao.com/b.png"></p>'
        urls = scraper._extract_image_urls(html)
        assert len(urls) == 2
        assert urls[0] == "https://xqimg.imedao.com/a.jpg"

    def test_no_images(self, scraper):
        assert scraper._extract_image_urls("<p>纯文字</p>") == []

    def test_none_input(self, scraper):
        assert scraper._extract_image_urls(None) == []


class TestNeedsFullFetch:
    def test_column_needs_fetch(self, scraper):
        post = {"type": "3", "is_column": True, "text": ""}
        assert scraper._needs_full_fetch(post) is True

    def test_short_post_no_fetch(self, scraper):
        post = {"type": "2", "is_column": False, "text": "有内容"}
        assert scraper._needs_full_fetch(post) is False

    def test_column_with_text_no_fetch(self, scraper):
        post = {"type": "3", "is_column": True, "text": "已有全文"}
        assert scraper._needs_full_fetch(post) is False
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py -v`
Expected: FAIL — `ModuleNotFoundError`

**Step 3: Write the implementation**

```python
# core/xueqiu_scraper.py
"""雪球内容爬虫"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import requests

from core.xueqiu_db import XueqiuDB

logger = logging.getLogger(__name__)

_BASE_URL = "https://xueqiu.com"
_TIMELINE_API = "/v4/statuses/user_timeline.json"
_SHOW_API = "/statuses/show.json"


class XueqiuScraper:
    def __init__(self, db_path: str, image_dir: str):
        self.db = XueqiuDB(db_path)
        self.image_dir = image_dir
        os.makedirs(image_dir, exist_ok=True)
        self._cookies: dict = {}
        self._headers: dict = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Origin": _BASE_URL,
            "Referer": f"{_BASE_URL}/",
        }
        # Sync progress tracking
        self.sync_status: str = "idle"  # idle | logging_in | syncing | done | error
        self.sync_progress: str = ""
        self.sync_count: int = 0

    def login_and_sync(self, user_id: int, headless: bool = False):
        """Open browser for login, then sync all posts."""
        self.sync_status = "logging_in"
        self.sync_progress = "等待登录..."

        try:
            from scrapling.fetchers import StealthyFetcher

            def extract_cookies(page):
                import asyncio
                # Wait for user to login — poll for 'u' cookie
                self.sync_progress = "请在浏览器中登录雪球..."
                for _ in range(300):  # 5 min timeout
                    cookies = asyncio.get_event_loop().run_until_complete(
                        page.context.cookies()
                    )
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    if "u" in cookie_dict:
                        self._cookies = cookie_dict
                        return page
                    asyncio.get_event_loop().run_until_complete(
                        page.wait_for_timeout(1000)
                    )
                raise TimeoutError("登录超时（5分钟）")

            StealthyFetcher.fetch(
                f"{_BASE_URL}/",
                headless=headless,
                page_action=extract_cookies,
            )

            self._sync_all(user_id)

        except Exception as e:
            self.sync_status = "error"
            self.sync_progress = f"错误: {e}"
            logger.error("Sync failed: %s", e)
            raise

    def _sync_all(self, user_id: int):
        """Fetch all timeline pages and save posts."""
        self.sync_status = "syncing"
        last_id = self.db.get_latest_post_id()
        incremental = last_id is not None

        page = 1
        total_saved = 0
        stop = False

        while not stop:
            self.sync_progress = f"正在拉取第 {page} 页..."
            logger.info("Fetching timeline page %d", page)

            data = self._api_get(_TIMELINE_API, {
                "user_id": user_id, "page": page,
            })
            if not data:
                break

            posts = self._parse_timeline(data)
            if not posts:
                break

            for post in posts:
                # Incremental: stop if we've seen this post
                if incremental and self.db.get_post(post["id"]):
                    stop = True
                    break

                # Fetch full article for columns
                if self._needs_full_fetch(post):
                    full = self._fetch_full_article(post["id"])
                    if full:
                        post["text"] = full.get("text", post.get("text", ""))
                        post["title"] = full.get("title", post.get("title"))

                # Download images
                img_urls = self._extract_image_urls(post.get("text"))
                self.db.save_post(post)
                for seq, url in enumerate(img_urls):
                    local = self._download_image(post["id"], url, seq)
                    self.db.save_image(post["id"], url, local, seq)

                total_saved += 1
                self.sync_count = total_saved
                self.sync_progress = f"已保存 {total_saved} 条"

            page += 1
            time.sleep(1.5)  # Rate limiting

        self.db.set_sync_state("last_sync_time", str(int(time.time())))
        self.db.set_sync_state("total_synced", str(total_saved))
        self.sync_status = "done"
        self.sync_progress = f"同步完成，共 {total_saved} 条"
        logger.info("Sync complete: %d posts saved", total_saved)

    def _api_get(self, path: str, params: dict) -> Optional[dict]:
        """Make authenticated API request."""
        try:
            resp = requests.get(
                f"{_BASE_URL}{path}",
                params=params,
                cookies=self._cookies,
                headers=self._headers,
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("API request failed: %s %s — %s", path, params, e)
            return None

    def _parse_timeline(self, data: dict) -> List[Dict]:
        posts = []
        for s in data.get("statuses", []):
            post = {
                "id": s["id"],
                "user_id": s.get("user_id", 0),
                "type": s.get("type"),
                "is_column": bool(s.get("is_column")),
                "title": s.get("title") or None,
                "text": s.get("text") or "",
                "description": s.get("description") or "",
                "created_at": s.get("created_at", 0),
                "edited_at": s.get("edited_at") or None,
                "target": s.get("target") or "",
                "retweet_status_id": s.get("retweet_status_id", 0),
                "retweet_text": None,
                "reply_count": s.get("reply_count", 0),
                "like_count": s.get("like_count", 0),
                "retweet_count": s.get("retweet_count", 0),
                "view_count": s.get("view_count", 0),
            }
            # Extract retweet original text
            rt = s.get("retweeted_status")
            if rt and isinstance(rt, dict):
                post["retweet_text"] = rt.get("text") or None
            posts.append(post)
        return posts

    def _needs_full_fetch(self, post: dict) -> bool:
        return (
            str(post.get("type")) == "3"
            and post.get("is_column")
            and not post.get("text")
        )

    def _fetch_full_article(self, post_id: int) -> Optional[dict]:
        data = self._api_get(_SHOW_API, {"id": post_id})
        if not data:
            return None
        return data

    def _extract_image_urls(self, html: Optional[str]) -> List[str]:
        if not html:
            return []
        return re.findall(r'<img[^>]+src="([^"]+)"', html)

    def _download_image(self, post_id: int, url: str, seq: int) -> Optional[str]:
        try:
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix or ".jpg"
            filename = f"{seq:03d}{ext}"
            local_dir = Path(self.image_dir) / str(post_id)
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / filename

            resp = requests.get(url, headers=self._headers, timeout=15)
            resp.raise_for_status()
            local_path.write_bytes(resp.content)
            return str(local_path)
        except Exception as e:
            logger.warning("Image download failed: %s — %s", url, e)
            return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add core/xueqiu_scraper.py tests/test_xueqiu_scraper.py
git commit -m "feat: add XueqiuScraper with timeline parsing and image download"
```

---

### Task 4: CLI 入口脚本

**Files:**
- Create: `scripts/sync_xueqiu.py`

**Step 1: Write the script**

```python
# scripts/sync_xueqiu.py
"""雪球内容同步 CLI"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.xueqiu_scraper import XueqiuScraper

_DEFAULT_BASE = os.path.expanduser("~/.investment-assistant/data")


def main():
    parser = argparse.ArgumentParser(description="同步雪球用户动态")
    parser.add_argument("--user-id", type=int, required=True, help="雪球用户 ID")
    parser.add_argument("--headless", action="store_true", help="无头模式（调试用）")
    parser.add_argument("--db-path", default=os.path.join(_DEFAULT_BASE, "xueqiu_posts.db"))
    parser.add_argument("--image-dir", default=os.path.join(_DEFAULT_BASE, "xueqiu_images"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scraper = XueqiuScraper(args.db_path, args.image_dir)
    scraper.login_and_sync(args.user_id, headless=args.headless)


if __name__ == "__main__":
    main()
```

**Step 2: Verify script loads**

Run: `uv run python scripts/sync_xueqiu.py --help`
Expected: 显示 argparse 帮助信息

**Step 3: Commit**

```bash
git add scripts/sync_xueqiu.py
git commit -m "feat: add xueqiu sync CLI script"
```

---

### Task 5: Flask API 路由

**Files:**
- Modify: `web/app.py`

**Step 1: Write the failing test**

```python
# tests/test_xueqiu_api.py
"""Tests for xueqiu API routes."""

import json
import sqlite3
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def app_client(tmp_path):
    """Create a Flask test client with tmp storage."""
    import web.app as webapp

    # Override storage base_dir
    from core.storage import Storage
    webapp.storage = Storage(base_dir=str(tmp_path / "inv"))

    # Create a test DB with some posts
    db_path = tmp_path / "inv" / "data" / "xueqiu_posts.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from core.xueqiu_db import XueqiuDB
    db = XueqiuDB(str(db_path))
    for i in range(3):
        db.save_post({
            "id": i + 1, "user_id": 1936609590, "type": "2",
            "title": f"Title {i}" if i == 0 else None,
            "text": f"<p>Content {i} 铜价分析</p>" if i == 0 else f"<p>Content {i}</p>",
            "description": f"Desc {i}",
            "created_at": 1709856000000 + i * 86400000,
            "retweet_status_id": 100 if i == 2 else 0,
        })

    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as client:
        yield client


class TestListPosts:
    def test_list_all(self, app_client):
        resp = app_client.get("/api/xueqiu/posts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 3
        assert len(data["posts"]) == 3

    def test_pagination(self, app_client):
        resp = app_client.get("/api/xueqiu/posts?per_page=2&page=1")
        data = resp.get_json()
        assert len(data["posts"]) == 2
        assert data["total"] == 3

    def test_search(self, app_client):
        resp = app_client.get("/api/xueqiu/posts?q=铜价")
        data = resp.get_json()
        assert data["total"] == 1

    def test_filter_retweet(self, app_client):
        resp = app_client.get("/api/xueqiu/posts?type=retweet")
        data = resp.get_json()
        assert data["total"] == 1


class TestGetPost:
    def test_get_existing(self, app_client):
        resp = app_client.get("/api/xueqiu/posts/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == 1

    def test_get_missing(self, app_client):
        resp = app_client.get("/api/xueqiu/posts/999")
        assert resp.status_code == 404


class TestSyncStatus:
    def test_idle(self, app_client):
        resp = app_client.get("/api/xueqiu/sync/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "idle"
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_xueqiu_api.py -v`
Expected: FAIL — routes not defined

**Step 3: Add routes to web/app.py**

Add the following after the 利润跟踪 API section (after line ~824) in `web/app.py`:

```python
# ==================== 雪球跟踪 API ====================

import threading

_xueqiu_scraper = None  # Lazy init
_xueqiu_sync_thread = None


def _get_xueqiu_db():
    from core.xueqiu_db import XueqiuDB
    db_path = os.path.join(str(storage.base_dir), "data", "xueqiu_posts.db")
    return XueqiuDB(db_path)


def _get_xueqiu_scraper():
    global _xueqiu_scraper
    if _xueqiu_scraper is None:
        from core.xueqiu_scraper import XueqiuScraper
        db_path = os.path.join(str(storage.base_dir), "data", "xueqiu_posts.db")
        image_dir = os.path.join(str(storage.base_dir), "data", "xueqiu_images")
        _xueqiu_scraper = XueqiuScraper(db_path, image_dir)
    return _xueqiu_scraper


@app.route('/xueqiu')
@requires_auth
def xueqiu_page():
    return render_template('xueqiu.html')


@app.route('/api/xueqiu/posts', methods=['GET'])
@requires_auth
def api_xueqiu_list_posts():
    db = _get_xueqiu_db()
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)
    post_type = request.args.get('type', 'all')
    query = request.args.get('q', '')
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    posts, total = db.list_posts(
        page=page, per_page=per_page, post_type=post_type,
        query=query, start_date=start_date, end_date=end_date,
    )
    return jsonify({"posts": posts, "total": total, "page": page, "per_page": per_page})


@app.route('/api/xueqiu/posts/<int:post_id>', methods=['GET'])
@requires_auth
def api_xueqiu_get_post(post_id):
    db = _get_xueqiu_db()
    post = db.get_post(post_id)
    if not post:
        return jsonify({"error": "Post not found"}), 404
    post["images"] = db.get_images(post_id)
    return jsonify(post)


@app.route('/api/xueqiu/images/<int:post_id>/<filename>', methods=['GET'])
@requires_auth
def api_xueqiu_image(post_id, filename):
    image_dir = os.path.join(str(storage.base_dir), "data", "xueqiu_images", str(post_id))
    from flask import send_from_directory
    return send_from_directory(image_dir, filename)


@app.route('/api/xueqiu/sync', methods=['POST'])
@requires_auth
def api_xueqiu_sync():
    global _xueqiu_sync_thread
    scraper = _get_xueqiu_scraper()

    if scraper.sync_status == "syncing" or scraper.sync_status == "logging_in":
        return jsonify({"error": "同步正在进行中"}), 409

    data = request.json or {}
    user_id = data.get("user_id", 1936609590)

    def run_sync():
        try:
            scraper.login_and_sync(user_id, headless=False)
        except Exception as e:
            scraper.sync_status = "error"
            scraper.sync_progress = str(e)

    _xueqiu_sync_thread = threading.Thread(target=run_sync, daemon=True)
    _xueqiu_sync_thread.start()
    return jsonify({"success": True, "message": "同步已启动"})


@app.route('/api/xueqiu/sync/status', methods=['GET'])
@requires_auth
def api_xueqiu_sync_status():
    scraper = _get_xueqiu_scraper()
    db = _get_xueqiu_db()
    last_sync = db.get_sync_state("last_sync_time")
    return jsonify({
        "status": scraper.sync_status,
        "progress": scraper.sync_progress,
        "count": scraper.sync_count,
        "last_sync_time": last_sync,
    })
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_xueqiu_api.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add web/app.py tests/test_xueqiu_api.py
git commit -m "feat: add xueqiu API routes for posts, search, sync"
```

---

### Task 6: 导航栏入口

**Files:**
- Modify: `web/templates/base.html:48-50`

**Step 1: Add nav link**

在 `base.html` 导航栏中"利润跟踪"链接后面添加"雪球跟踪"：

```html
<a href="/xueqiu" class="px-3 py-2 rounded-md text-sm font-medium {% if request.endpoint == 'xueqiu_page' %}text-blue-600 bg-blue-50{% else %}text-gray-600 hover:text-gray-900 hover:bg-gray-50{% endif %}">
    雪球跟踪
</a>
```

**Step 2: Verify**

Run: `uv run python -c "from web.app import app; print([r.rule for r in app.url_map.iter_rules() if 'xueqiu' in r.rule])"`
Expected: 包含 `/xueqiu` 路由

**Step 3: Commit**

```bash
git add web/templates/base.html
git commit -m "feat: add xueqiu nav link to base template"
```

---

### Task 7: 雪球展示页面

**Files:**
- Create: `web/templates/xueqiu.html`

**Step 1: Create the template**

页面使用 Alpine.js + Tailwind，双栏布局：

- 顶部：标题 + 搜索框 + 同步按钮
- 左侧（35%）：筛选栏 + 帖子列表 + 加载更多
- 右侧（65%）：帖子阅读区

Alpine.js 状态：

```javascript
{
  posts: [],
  selectedId: null,
  selectedPost: null,
  filter: 'all',       // all|original|retweet|column
  query: '',
  startDate: '',
  endDate: '',
  page: 1,
  total: 0,
  hasMore: true,
  loading: false,
  syncing: false,
  syncProgress: '',
  lastSyncTime: '',
  debounceTimer: null,
}
```

关键交互：

1. `loadPosts()` — 调用 `/api/xueqiu/posts` 加载列表
2. `selectPost(id)` — 调用 `/api/xueqiu/posts/<id>` 加载全文，更新 URL hash
3. `onSearch()` — 300ms 防抖后重新加载
4. `onFilterChange()` — 重置 page=1 重新加载
5. `loadMore()` — page++ 追加到列表
6. `startSync()` — POST `/api/xueqiu/sync`，轮询 status
7. `init()` — 加载列表 + 从 URL hash 恢复选中状态

图片处理：
- 帖子 HTML 中的图片 src 替换为 `/api/xueqiu/images/{post_id}/{filename}`
- `onerror` fallback 到原始 CDN URL

转发帖：
- `retweet_text` 非空时，用 `<blockquote>` 引用块展示原文

时间格式化：
- `created_at` 是 Unix 毫秒时间戳，前端用 `new Date(ts).toLocaleDateString()` 格式化

**Step 2: Verify page renders**

启动服务后访问 `http://localhost:8100/xueqiu`，确认：
- 页面正常渲染
- 空状态显示"暂无数据"
- 同步按钮可点击

**Step 3: Commit**

```bash
git add web/templates/xueqiu.html
git commit -m "feat: add xueqiu dual-pane reading page"
```

---

### Task 8: 集成测试 + CLAUDE.md 更新

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Update CLAUDE.md**

在"最近重大变更"部分添加：

```markdown
- 2026-03-08: 雪球内容爬取与展示
  - 设计文档: `docs/plans/2026-03-08-xueqiu-scraper-design.md`
```

在"常用命令"部分添加：

```bash
# 雪球同步
uv run python scripts/sync_xueqiu.py --user-id 1936609590            # 全量同步
uv run python scripts/sync_xueqiu.py --user-id 1936609590 --incremental  # 增量同步
```

在"数据存储"部分添加：

```
- `data/xueqiu_posts.db`: 雪球帖子（SQLite + FTS5）
- `data/xueqiu_images/`: 雪球帖子图片
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with xueqiu scraper module"
```

---

### Task 7 详细模板参考

`web/templates/xueqiu.html` 的完整实现要点：

**页面结构（Alpine.js 组件）：**

```html
{% extends "base.html" %}
{% block title %}雪球跟踪{% endblock %}
{% block content %}
<div x-data="xueqiuReader()" x-init="init()">

  <!-- 顶部栏：标题 + 搜索 + 同步 -->
  <div class="flex justify-between items-center mb-4">
    <h1 class="text-2xl font-bold text-gray-900">雪球跟踪</h1>
    <div class="flex items-center gap-3">
      <input type="text" x-model="query" @input="onSearch()"
             placeholder="搜索帖子..." class="...">
      <button @click="startSync()" :disabled="syncing" class="...">
        <span x-show="!syncing">同步数据</span>
        <span x-show="syncing" x-text="syncProgress"></span>
      </button>
    </div>
  </div>

  <!-- 双栏 -->
  <div class="flex gap-0 bg-white rounded-xl border border-gray-200 overflow-hidden"
       style="height: calc(100vh - 180px);">

    <!-- 左侧列表 35% -->
    <div class="w-[35%] border-r border-gray-200 flex flex-col">
      <!-- 筛选栏 -->
      <div class="p-3 border-b border-gray-100 flex gap-2">
        <template x-for="f in ['all','original','retweet','column']">
          <button @click="setFilter(f)" :class="filter===f ? 'active' : ''"
                  x-text="filterLabel(f)" class="..."></button>
        </template>
      </div>
      <!-- 帖子列表（可滚动） -->
      <div class="flex-1 overflow-y-auto">
        <template x-for="p in posts" :key="p.id">
          <div @click="selectPost(p.id)"
               :class="selectedId === p.id ? 'bg-blue-50 border-l-2 border-blue-500' : ''"
               class="p-3 border-b border-gray-50 cursor-pointer hover:bg-gray-50">
            <div class="flex items-center gap-2 text-xs text-gray-400 mb-1">
              <span x-text="formatDate(p.created_at)"></span>
              <span x-text="typeLabel(p)" class="..."></span>
            </div>
            <div x-show="p.title" class="text-sm font-medium text-gray-800 truncate"
                 x-text="p.title"></div>
            <div class="text-xs text-gray-500 line-clamp-2"
                 x-text="p.description || stripHtml(p.text)"></div>
          </div>
        </template>
        <button x-show="hasMore" @click="loadMore()" class="...">加载更多</button>
      </div>
    </div>

    <!-- 右侧阅读区 65% -->
    <div class="w-[65%] overflow-y-auto">
      <!-- 空态 -->
      <div x-show="!selectedPost" class="...">选择一篇帖子开始阅读</div>
      <!-- 帖子内容 -->
      <div x-show="selectedPost" class="p-6">
        <h2 x-show="selectedPost?.title" x-text="selectedPost?.title"
            class="text-xl font-bold mb-3"></h2>
        <div class="flex items-center gap-4 text-xs text-gray-400 mb-4">
          <span x-text="formatDate(selectedPost?.created_at)"></span>
          <span x-text="'👍 ' + (selectedPost?.like_count || 0)"></span>
          <span x-text="'💬 ' + (selectedPost?.reply_count || 0)"></span>
          <span x-text="'🔄 ' + (selectedPost?.retweet_count || 0)"></span>
        </div>
        <!-- 转发原文引用 -->
        <blockquote x-show="selectedPost?.retweet_text"
                    class="border-l-4 border-gray-200 pl-4 mb-4 text-sm text-gray-600"
                    x-html="selectedPost?.retweet_text"></blockquote>
        <!-- 正文 -->
        <div class="prose max-w-none" x-html="processHtml(selectedPost?.text)"></div>
      </div>
    </div>
  </div>
</div>

<script>
function xueqiuReader() {
  return {
    posts: [], selectedId: null, selectedPost: null,
    filter: 'all', query: '', page: 1, total: 0,
    hasMore: true, loading: false,
    syncing: false, syncProgress: '', lastSyncTime: '',
    debounceTimer: null,

    async init() {
      await this.loadPosts();
      // Restore from URL hash
      const hash = location.hash.replace('#post-', '');
      if (hash) this.selectPost(parseInt(hash));
      // Poll sync status
      this.pollSyncStatus();
    },

    async loadPosts(append = false) {
      this.loading = true;
      const params = new URLSearchParams({
        page: this.page, per_page: 30,
        type: this.filter, q: this.query,
      });
      const resp = await fetch(`/api/xueqiu/posts?${params}`);
      const data = await resp.json();
      this.posts = append ? [...this.posts, ...data.posts] : data.posts;
      this.total = data.total;
      this.hasMore = this.posts.length < this.total;
      this.loading = false;
    },

    async selectPost(id) {
      this.selectedId = id;
      location.hash = `post-${id}`;
      const resp = await fetch(`/api/xueqiu/posts/${id}`);
      this.selectedPost = await resp.json();
    },

    onSearch() {
      clearTimeout(this.debounceTimer);
      this.debounceTimer = setTimeout(() => {
        this.page = 1;
        this.loadPosts();
      }, 300);
    },

    setFilter(f) {
      this.filter = f;
      this.page = 1;
      this.loadPosts();
    },

    loadMore() {
      this.page++;
      this.loadPosts(true);
    },

    async startSync() {
      this.syncing = true;
      this.syncProgress = '启动中...';
      await fetch('/api/xueqiu/sync', { method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ user_id: 1936609590 }),
      });
      this.pollSyncStatus();
    },

    async pollSyncStatus() {
      const resp = await fetch('/api/xueqiu/sync/status');
      const data = await resp.json();
      this.syncProgress = data.progress || '';
      this.lastSyncTime = data.last_sync_time || '';
      if (data.status === 'syncing' || data.status === 'logging_in') {
        this.syncing = true;
        setTimeout(() => this.pollSyncStatus(), 2000);
      } else {
        if (this.syncing && data.status === 'done') {
          this.page = 1;
          this.loadPosts();
        }
        this.syncing = false;
      }
    },

    processHtml(html) {
      if (!html) return '';
      // Replace image src with local path, add onerror fallback
      return html.replace(
        /<img([^>]*)src="([^"]+)"([^>]*)>/g,
        (match, pre, src, post) => {
          const postId = this.selectedPost?.id;
          if (!postId) return match;
          const filename = src.split('/').pop();
          const localSrc = `/api/xueqiu/images/${postId}/${filename}`;
          return `<img${pre}src="${localSrc}" onerror="this.src='${src}'"${post}>`;
        }
      );
    },

    formatDate(ts) {
      if (!ts) return '';
      return new Date(ts).toLocaleDateString('zh-CN', {
        month: '2-digit', day: '2-digit',
      });
    },

    typeLabel(p) {
      if (p.is_column) return '长文';
      if (p.retweet_status_id) return '转发';
      return '原创';
    },

    filterLabel(f) {
      return { all: '全部', original: '原创', retweet: '转发', column: '长文' }[f];
    },

    stripHtml(html) {
      if (!html) return '';
      return html.replace(/<[^>]+>/g, '').slice(0, 100);
    },
  };
}
</script>
{% endblock %}
```

**注意事项：**
- 图片 `processHtml` 中的替换逻辑需要匹配 `xueqiu_scraper._download_image` 的命名规则（`{seq:03d}{ext}`）
- 实际实现时需要根据 images 表中的映射关系来替换，而非简单的文件名提取
- 可以在 `selectPost` 时用 images 列表构建 URL 映射表

---

## 任务依赖关系

```
Task 1 (依赖) ──→ Task 2 ──→ Task 3 ──→ Task 4
                      │                     │
                      └──→ Task 5 ──→ Task 6 ──→ Task 7 ──→ Task 8
```

- Task 1 必须先完成（安装 scrapling）
- Task 2（DB 层）是 Task 3（爬虫）和 Task 5（API）的前置
- Task 3（爬虫）是 Task 4（CLI）的前置
- Task 5（API）是 Task 7（页面）的前置
- Task 6（导航栏）可以和 Task 5 并行
- Task 8（集成测试）最后执行
