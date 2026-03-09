# 雪球 V2：多用户爬取 + AI 聊天助手 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将雪球爬取产品化（多用户、批次控制、进度展示），新增 AI 聊天助手页面（SSE 流式、思考过程、会话管理）。

**Architecture:** 在现有 XueqiuDB 中新增 xueqiu_users 表，XueqiuScraper 增加 max_pages 参数。新建 core/chat.py 包含 ChatDB（独立 chat.db）和 ChatEngine（SSE 流式 + 上下文构建）。前端复用 Alpine.js + Tailwind 技术栈。

**Tech Stack:** Python 3.10+, Flask, SQLite, OpenAI SDK (streaming), Alpine.js, Tailwind CSS, marked.js, DOMPurify

**Design doc:** `docs/plans/2026-03-09-xueqiu-v2-chat-design.md`

---

### Task 1: XueqiuDB 用户管理

**Files:**
- Modify: `core/xueqiu_db.py`
- Test: `tests/test_xueqiu_db.py`

**Step 1: Write failing tests for user CRUD**

Add to `tests/test_xueqiu_db.py`:

```python
class TestUserManagement:
    def test_add_and_get_user(self, db):
        db.add_user(1936609590, "逸修1")
        user = db.get_user(1936609590)
        assert user is not None
        assert user["nickname"] == "逸修1"
        assert user["is_active"] == 1

    def test_add_duplicate_user(self, db):
        db.add_user(1936609590, "逸修1")
        db.add_user(1936609590, "逸修1改名")
        user = db.get_user(1936609590)
        assert user["nickname"] == "逸修1改名"

    def test_list_users(self, db):
        db.add_user(1, "用户A")
        db.add_user(2, "用户B")
        users = db.list_users()
        assert len(users) == 2

    def test_remove_user(self, db):
        db.add_user(1, "用户A")
        db.remove_user(1)
        assert db.get_user(1) is None

    def test_list_users_empty(self, db):
        assert db.list_users() == []

    def test_xueqiu_users_table_created(self, db):
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "xueqiu_users" in tables
        conn.close()
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_xueqiu_db.py::TestUserManagement -v`
Expected: FAIL — `AttributeError: 'XueqiuDB' object has no attribute 'add_user'`

**Step 3: Implement user management in XueqiuDB**

Add to `core/xueqiu_db.py` `_init_schema` method (inside the `executescript` string, after `sync_state` table):

```sql
CREATE TABLE IF NOT EXISTS xueqiu_users (
    user_id INTEGER PRIMARY KEY,
    nickname TEXT NOT NULL,
    avatar_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
);
```

Add methods to `XueqiuDB` class:

```python
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
```

**Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_xueqiu_db.py::TestUserManagement -v`
Expected: All 6 tests PASS

**Step 5: Add user_id filter to list_posts**

Modify `list_posts` in `core/xueqiu_db.py` — add `user_id` parameter:

```python
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
```

Inside the method, after the date range conditions block, add:

```python
if user_id is not None:
    conditions.append("user_id = ?")
    params.append(user_id)
```

**Step 6: Add test for user_id filter**

Add to `TestListPosts` in `tests/test_xueqiu_db.py`:

```python
def test_filter_by_user_id(self, db):
    db.save_post({
        "id": 100, "user_id": 999, "type": "2",
        "text": "Other user", "description": "Other",
        "created_at": 9000,
    })
    posts, total = db.list_posts(user_id=999)
    assert total == 1
    assert posts[0]["user_id"] == 999
```

**Step 7: Run all xueqiu_db tests**

Run: `uv run python -m pytest tests/test_xueqiu_db.py -v`
Expected: All tests PASS

**Step 8: Commit**

```bash
git add core/xueqiu_db.py tests/test_xueqiu_db.py
git commit -m "feat(xueqiu): add user management CRUD and user_id filter to list_posts"
```

---

### Task 2: XueqiuScraper max_pages 参数

**Files:**
- Modify: `core/xueqiu_scraper.py`
- Test: `tests/test_xueqiu_scraper.py`

**Step 1: Write failing test for max_pages**

Add to `tests/test_xueqiu_scraper.py`:

```python
class TestMaxPages:
    def test_default_max_pages(self, scraper):
        """login_and_sync 默认 max_pages=5"""
        import inspect
        sig = inspect.signature(scraper.login_and_sync)
        assert sig.parameters["max_pages"].default == 5

    def test_sync_all_respects_max_pages(self, scraper):
        """_sync_all 在达到 max_pages 后停止"""
        import json as _json
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.content.return_value = ""

        call_count = 0
        def fake_evaluate(js_code):
            nonlocal call_count
            # _ensure_waf_ready calls evaluate too, return True for those
            if "document.querySelector" in js_code:
                return True
            # For API calls, return timeline JSON
            call_count += 1
            return _json.dumps({"statuses": [
                {"id": call_count * 100 + i, "user_id": 1, "type": "2",
                 "text": f"post {i}", "description": f"post {i}",
                 "created_at": 1000 + call_count * 100 + i}
                for i in range(3)
            ]})

        mock_page.evaluate.side_effect = fake_evaluate
        mock_page.title.return_value = "雪球"
        mock_page.context = MagicMock()
        mock_page.context.cookies.return_value = []
        mock_page.wait_for_timeout = MagicMock()

        scraper._sync_all(mock_page, user_id=1, max_pages=2)
        assert scraper.sync_status == "done"
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestMaxPages -v`
Expected: FAIL — `TypeError: _sync_all() got an unexpected keyword argument 'max_pages'`

**Step 3: Add max_pages parameter**

Modify `core/xueqiu_scraper.py`:

1. Change `login_and_sync` signature (line 50):
```python
def login_and_sync(self, user_id: int, headless: bool = False, max_pages: int = 5):
```

2. Change the call to `_sync_all` (line 97):
```python
self._sync_all(page, user_id, max_pages=max_pages)
```

3. Change `_sync_all` signature (line 109):
```python
def _sync_all(self, page, user_id: int, max_pages: int = 5):
```

4. Remove the hardcoded `max_pages = 200` (line 122) — it's now a parameter.

5. Update progress message (line 127):
```python
self.sync_progress = f"正在拉取第 {pg}/{max_pages} 页..."
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add core/xueqiu_scraper.py tests/test_xueqiu_scraper.py
git commit -m "feat(xueqiu): add max_pages parameter to control sync batch size"
```

---

### Task 3: ChatDB — 聊天记录存储

**Files:**
- Create: `core/chat.py`
- Create: `tests/test_chat_db.py`

**Step 1: Write failing tests**

Create `tests/test_chat_db.py`:

```python
"""Tests for ChatDB."""

import pytest


@pytest.fixture()
def chat_db(tmp_path):
    from core.chat import ChatDB
    return ChatDB(str(tmp_path / "chat.db"))


class TestSchema:
    def test_tables_created(self, chat_db):
        import sqlite3
        conn = sqlite3.connect(chat_db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "chat_sessions" in tables
        assert "chat_messages" in tables
        conn.close()


class TestSessions:
    def test_create_session(self, chat_db):
        sid = chat_db.create_session()
        assert sid is not None
        assert len(sid) == 36  # UUID format

    def test_list_sessions(self, chat_db):
        chat_db.create_session()
        chat_db.create_session()
        sessions = chat_db.list_sessions()
        assert len(sessions) == 2
        # 按 updated_at 倒序
        assert sessions[0]["updated_at"] >= sessions[1]["updated_at"]

    def test_delete_session(self, chat_db):
        sid = chat_db.create_session()
        chat_db.delete_session(sid)
        assert chat_db.list_sessions() == []

    def test_update_session_title(self, chat_db):
        sid = chat_db.create_session()
        chat_db.update_session_title(sid, "逸修1观点总结")
        sessions = chat_db.list_sessions()
        assert sessions[0]["title"] == "逸修1观点总结"


class TestMessages:
    def test_add_and_get_messages(self, chat_db):
        sid = chat_db.create_session()
        chat_db.add_message(sid, "user", "你好")
        chat_db.add_message(sid, "assistant", "你好！有什么可以帮你的？")
        msgs = chat_db.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_add_thinking_message(self, chat_db):
        sid = chat_db.create_session()
        chat_db.add_message(sid, "thinking", "正在分析...")
        chat_db.add_message(sid, "assistant", "分析结果")
        msgs = chat_db.get_messages(sid)
        assert len(msgs) == 2
        assert msgs[0]["role"] == "thinking"

    def test_delete_session_cascades_messages(self, chat_db):
        sid = chat_db.create_session()
        chat_db.add_message(sid, "user", "test")
        chat_db.delete_session(sid)
        assert chat_db.get_messages(sid) == []

    def test_add_message_updates_session_timestamp(self, chat_db):
        sid = chat_db.create_session()
        sessions_before = chat_db.list_sessions()
        import time
        time.sleep(0.01)
        chat_db.add_message(sid, "user", "hello")
        sessions_after = chat_db.list_sessions()
        assert sessions_after[0]["updated_at"] >= sessions_before[0]["updated_at"]
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_chat_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.chat'`

**Step 3: Implement ChatDB**

Create `core/chat.py`:

```python
"""AI 聊天引擎：ChatDB（存储）+ ChatEngine（流式对话）"""

import contextlib
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional


class ChatDB:
    """聊天记录 SQLite 存储。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextlib.contextmanager
    def _get_conn(self):
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
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '新对话',
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
            """)

    def create_session(self) -> str:
        sid = str(uuid.uuid4())
        now = int(time.time() * 1000)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_sessions (id, title, created_at, updated_at) VALUES (?, '新对话', ?, ?)",
                (sid, now, now),
            )
            conn.commit()
        return sid

    def list_sessions(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_session(self, session_id: str) -> None:
        with self._get_conn() as conn:
            conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
            conn.commit()

    def update_session_title(self, session_id: str, title: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "UPDATE chat_sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, int(time.time() * 1000), session_id),
            )
            conn.commit()

    def add_message(self, session_id: str, role: str, content: str) -> None:
        now = int(time.time() * 1000)
        with self._get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_id, role, content, now),
            )
            conn.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            conn.commit()

    def get_messages(self, session_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            return [dict(r) for r in rows]
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_chat_db.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add core/chat.py tests/test_chat_db.py
git commit -m "feat(chat): add ChatDB for session and message storage"
```

---

### Task 4: ChatEngine — 流式对话引擎

**Files:**
- Modify: `core/chat.py`
- Create: `tests/test_chat_engine.py`

**Step 1: Write failing tests**

Create `tests/test_chat_engine.py`:

```python
"""Tests for ChatEngine."""

from unittest.mock import MagicMock, patch
import pytest


@pytest.fixture()
def chat_db(tmp_path):
    from core.chat import ChatDB
    return ChatDB(str(tmp_path / "chat.db"))


@pytest.fixture()
def xueqiu_db(tmp_path):
    from core.xueqiu_db import XueqiuDB
    db = XueqiuDB(str(tmp_path / "xueqiu.db"))
    # Seed some posts
    db.save_post({
        "id": 1, "user_id": 1936609590, "type": "2",
        "text": "<p>看好铜价长期走势</p>", "description": "看好铜价长期走势",
        "created_at": 1709856000000,
    })
    db.save_post({
        "id": 2, "user_id": 1936609590, "type": "2",
        "text": "<p>AI算力需求持续增长</p>", "description": "AI算力需求持续增长",
        "created_at": 1709856100000,
    })
    db.add_user(1936609590, "逸修1")
    return db


@pytest.fixture()
def engine(chat_db, xueqiu_db):
    from core.chat import ChatEngine
    mock_llm = MagicMock()
    return ChatEngine(llm_client=mock_llm, xueqiu_db=xueqiu_db, chat_db=chat_db)


class TestContextBuilding:
    def test_build_context_includes_system_prompt(self, engine, chat_db):
        sid = chat_db.create_session()
        messages = engine._build_messages(sid, "总结逸修1的观点")
        assert messages[0]["role"] == "system"
        assert "投研" in messages[0]["content"]

    def test_build_context_includes_relevant_posts(self, engine, chat_db):
        sid = chat_db.create_session()
        messages = engine._build_messages(sid, "铜价分析")
        system_content = messages[0]["content"]
        assert "铜价" in system_content

    def test_build_context_includes_history(self, engine, chat_db):
        sid = chat_db.create_session()
        chat_db.add_message(sid, "user", "你好")
        chat_db.add_message(sid, "assistant", "你好！")
        messages = engine._build_messages(sid, "继续")
        # system + history(2) + user
        assert len(messages) == 4


class TestStreamReply:
    def test_stream_yields_events(self, engine, chat_db):
        sid = chat_db.create_session()

        # Mock streaming response
        mock_chunk1 = MagicMock()
        mock_chunk1.choices = [MagicMock()]
        mock_chunk1.choices[0].delta.content = "你好"
        mock_chunk1.choices[0].delta.reasoning_content = None

        mock_chunk2 = MagicMock()
        mock_chunk2.choices = [MagicMock()]
        mock_chunk2.choices[0].delta.content = "世界"
        mock_chunk2.choices[0].delta.reasoning_content = None

        engine.llm.client.chat.completions.create.return_value = iter([mock_chunk1, mock_chunk2])

        events = list(engine.stream_reply(sid, "测试"))
        types = [e["type"] for e in events]
        assert "content" in types
        assert "done" in types

    def test_stream_saves_messages(self, engine, chat_db):
        sid = chat_db.create_session()

        mock_chunk = MagicMock()
        mock_chunk.choices = [MagicMock()]
        mock_chunk.choices[0].delta.content = "回复"
        mock_chunk.choices[0].delta.reasoning_content = None

        engine.llm.client.chat.completions.create.return_value = iter([mock_chunk])

        list(engine.stream_reply(sid, "问题"))
        msgs = chat_db.get_messages(sid)
        roles = [m["role"] for m in msgs]
        assert "user" in roles
        assert "assistant" in roles

    def test_stream_handles_thinking(self, engine, chat_db):
        sid = chat_db.create_session()

        mock_think = MagicMock()
        mock_think.choices = [MagicMock()]
        mock_think.choices[0].delta.content = None
        mock_think.choices[0].delta.reasoning_content = "思考中..."

        mock_reply = MagicMock()
        mock_reply.choices = [MagicMock()]
        mock_reply.choices[0].delta.content = "结论"
        mock_reply.choices[0].delta.reasoning_content = None

        engine.llm.client.chat.completions.create.return_value = iter([mock_think, mock_reply])

        events = list(engine.stream_reply(sid, "分析"))
        types = [e["type"] for e in events]
        assert "thinking" in types
        assert "content" in types
```

**Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_chat_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'ChatEngine' from 'core.chat'`

**Step 3: Implement ChatEngine**

Append to `core/chat.py`:

```python
import json
import logging
import re

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """你是一位专业的投资研究分析师助手。你可以访问以下数据源：

1. 雪球用户帖子 — 包含多位投资者的原创观点、转发评论和长文分析
2. 个股研究记录 — 包含投资逻辑(playbook)、研究历史、利润模型
3. 投资组合策略 — 总体投资框架和偏好

你的职责：
- 基于数据源中的真实内容进行分析，不要编造
- 总结投资者的核心观点时，引用具体帖子内容作为依据
- 对比不同时期的观点变化
- 用中文回复，语言简洁专业

以下是与用户问题相关的数据：
"""


class ChatEngine:
    """AI 聊天引擎，支持 SSE 流式输出。"""

    def __init__(self, llm_client, xueqiu_db, chat_db: ChatDB,
                 storage=None):
        self.llm = llm_client
        self.xueqiu_db = xueqiu_db
        self.chat_db = chat_db
        self.storage = storage

    def stream_reply(self, session_id: str, user_message: str):
        """生成器，yield SSE 事件 dict。"""
        # 保存用户消息
        self.chat_db.add_message(session_id, "user", user_message)

        # 构建消息
        messages = self._build_messages(session_id, user_message)

        # 流式调用 LLM
        thinking_buf = []
        content_buf = []
        try:
            stream = self.llm.client.chat.completions.create(
                model=self.llm.model,
                messages=messages,
                stream=True,
                timeout=300,
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                # 思考过程（reasoning_content，Gemini/OpenAI thinking models）
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    thinking_buf.append(reasoning)
                    yield {"type": "thinking", "content": reasoning}

                # 正文内容
                text = delta.content
                if text:
                    content_buf.append(text)
                    yield {"type": "content", "content": text}

        except Exception as e:
            logger.error("ChatEngine stream error: %s", e)
            yield {"type": "error", "content": str(e)}
            return

        # 保存消息
        if thinking_buf:
            self.chat_db.add_message(session_id, "thinking", "".join(thinking_buf))
        if content_buf:
            self.chat_db.add_message(session_id, "assistant", "".join(content_buf))

        # 首轮对话自动生成标题
        all_msgs = self.chat_db.get_messages(session_id)
        user_msgs = [m for m in all_msgs if m["role"] == "user"]
        session_title = None
        if len(user_msgs) == 1:
            session_title = self._generate_title(user_message, "".join(content_buf))

        yield {"type": "done", "session_title": session_title}

    def _build_messages(self, session_id: str, user_message: str) -> list[dict]:
        """构建 LLM 消息列表：system + 历史 + 用户消息。"""
        # 系统 prompt + 相关帖子上下文
        context_parts = [_SYSTEM_PROMPT]

        # 搜索相关帖子
        posts_context = self._search_relevant_posts(user_message)
        if posts_context:
            context_parts.append("\n--- 雪球帖子 ---\n")
            context_parts.append(posts_context)

        # 加载其他系统数据（playbook 等）
        if self.storage:
            extra = self._load_system_context(user_message)
            if extra:
                context_parts.append("\n--- 系统数据 ---\n")
                context_parts.append(extra)

        messages = [{"role": "system", "content": "".join(context_parts)}]

        # 历史消息（不含 thinking）
        history = self.chat_db.get_messages(session_id)
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        # 当前用户消息
        messages.append({"role": "user", "content": user_message})
        return messages

    def _search_relevant_posts(self, query: str) -> str:
        """从雪球帖子中搜索相关内容，构建上下文字符串。"""
        parts = []

        # 检查是否提到了特定用户
        users = self.xueqiu_db.list_users()
        target_user_id = None
        for u in users:
            if u["nickname"] in query:
                target_user_id = u["user_id"]
                break

        if target_user_id:
            # 拉取该用户全部帖子摘要
            posts, _ = self.xueqiu_db.list_posts(
                user_id=target_user_id, per_page=500
            )
            for p in posts:
                desc = p.get("description") or ""
                title = p.get("title") or ""
                ts = p.get("created_at", 0)
                date_str = ""
                if ts:
                    from datetime import datetime, timezone, timedelta
                    dt = datetime.fromtimestamp(ts / 1000, tz=timezone(timedelta(hours=8)))
                    date_str = dt.strftime("%Y-%m-%d")
                header = f"[{date_str}]"
                if title:
                    header += f" {title}"
                parts.append(f"{header}\n{desc}\n")
        else:
            # 关键词搜索
            posts, _ = self.xueqiu_db.list_posts(query=query, per_page=50)
            for p in posts:
                desc = p.get("description") or ""
                title = p.get("title") or ""
                parts.append(f"{title}\n{desc}\n" if title else f"{desc}\n")

        return "\n".join(parts) if parts else ""

    def _load_system_context(self, query: str) -> str:
        """从 Storage 加载相关系统数据。"""
        if not self.storage:
            return ""
        parts = []
        try:
            portfolio = self.storage.get_portfolio_playbook()
            if portfolio:
                parts.append(f"投资组合策略:\n{json.dumps(portfolio, ensure_ascii=False, indent=2)}\n")
        except Exception:
            pass
        return "\n".join(parts)

    def _generate_title(self, user_msg: str, assistant_reply: str) -> Optional[str]:
        """用 LLM 生成会话标题。"""
        try:
            title = self.llm.chat(
                f"根据以下对话生成一个简短的中文标题（10字以内，不要引号）：\n"
                f"用户：{user_msg[:200]}\n助手：{assistant_reply[:200]}"
            )
            title = title.strip().strip('"\'')[:20]
            if title:
                sessions = self.chat_db.list_sessions()
                if sessions:
                    self.chat_db.update_session_title(sessions[0]["id"], title)
            return title
        except Exception:
            return None
```

**Step 4: Run tests**

Run: `uv run python -m pytest tests/test_chat_engine.py -v`
Expected: All tests PASS

**Step 5: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 6: Commit**

```bash
git add core/chat.py tests/test_chat_engine.py
git commit -m "feat(chat): add ChatEngine with SSE streaming and context building"
```

---

### Task 5: 雪球用户管理 API + 同步 API 扩展

**Files:**
- Modify: `web/app.py`

**Step 1: Add user management routes**

In `web/app.py`, after the existing xueqiu sync status route (~line 1060), add:

```python
@app.route('/api/xueqiu/users', methods=['GET'])
@requires_auth
def api_xueqiu_list_users():
    db = _get_xueqiu_db()
    return jsonify({"users": db.list_users()})


@app.route('/api/xueqiu/users', methods=['POST'])
@requires_auth
def api_xueqiu_add_user():
    data = request.json or {}
    user_id = data.get("user_id")
    nickname = data.get("nickname", "").strip()
    if not user_id or not isinstance(user_id, int) or user_id <= 0:
        return jsonify({"error": "user_id 必须为正整数"}), 400
    if not nickname:
        return jsonify({"error": "昵称不能为空"}), 400
    db = _get_xueqiu_db()
    existing = db.get_user(user_id)
    if existing:
        return jsonify({"error": "用户已存在"}), 409
    db.add_user(user_id, nickname)
    return jsonify({"success": True, "user": db.get_user(user_id)})


@app.route('/api/xueqiu/users/<int:user_id>', methods=['DELETE'])
@requires_auth
def api_xueqiu_remove_user(user_id):
    db = _get_xueqiu_db()
    db.remove_user(user_id)
    return jsonify({"success": True})
```

**Step 2: Modify sync route to accept max_pages and user_id**

Update the existing `api_xueqiu_sync` function in `web/app.py` (~line 1016):

Change the `run_sync` inner function to pass `max_pages`:

```python
user_id = data.get("user_id", 1936609590)
max_pages = min(data.get("max_pages", 5), 200)  # 上限 200 页

def run_sync():
    try:
        scraper.login_and_sync(user_id, headless=False, max_pages=max_pages)
    except Exception as e:
        scraper.sync_status = "error"
        scraper.sync_progress = str(e)
    finally:
        _xueqiu_sync_lock.release()
```

**Step 3: Add user_id filter to posts list API**

Update `api_xueqiu_list_posts` to read `user_id` param:

```python
user_id = request.args.get('user_id', type=int) or None
posts, total = db.list_posts(
    page=page, per_page=per_page, post_type=post_type,
    query=query, start_date=start_date, end_date=end_date,
    user_id=user_id,
)
```

**Step 4: Run existing tests to verify no regressions**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add web/app.py
git commit -m "feat(xueqiu): add user management API and extend sync with max_pages"
```

---

### Task 6: 聊天 API 路由

**Files:**
- Modify: `web/app.py`
- Modify: `web/templates/base.html`

**Step 1: Add chat routes to app.py**

After the xueqiu routes section, add a new section:

```python
# ==================== AI 聊天 API ====================

_chat_engine = None
_chat_db = None


def _get_chat_db():
    global _chat_db
    if _chat_db is None:
        from core.chat import ChatDB
        db_path = os.path.join(str(storage.base_dir), "data", "chat.db")
        _chat_db = ChatDB(db_path)
    return _chat_db


def _get_chat_engine():
    global _chat_engine
    if _chat_engine is None:
        from core.chat import ChatEngine
        _chat_engine = ChatEngine(
            llm_client=get_client(),
            xueqiu_db=_get_xueqiu_db(),
            chat_db=_get_chat_db(),
            storage=storage,
        )
    return _chat_engine


@app.route('/chat')
@requires_auth
def chat_page():
    return render_template('chat.html')


@app.route('/api/chat/sessions', methods=['GET'])
@requires_auth
def api_chat_list_sessions():
    db = _get_chat_db()
    return jsonify({"sessions": db.list_sessions()})


@app.route('/api/chat/sessions', methods=['POST'])
@requires_auth
def api_chat_create_session():
    db = _get_chat_db()
    sid = db.create_session()
    sessions = db.list_sessions()
    session_data = next((s for s in sessions if s["id"] == sid), None)
    return jsonify(session_data)


@app.route('/api/chat/sessions/<session_id>', methods=['DELETE'])
@requires_auth
def api_chat_delete_session(session_id):
    db = _get_chat_db()
    db.delete_session(session_id)
    return jsonify({"success": True})


@app.route('/api/chat/sessions/<session_id>/messages', methods=['GET'])
@requires_auth
def api_chat_get_messages(session_id):
    db = _get_chat_db()
    return jsonify({"messages": db.get_messages(session_id)})


@app.route('/api/chat/sessions/<session_id>/messages', methods=['POST'])
@requires_auth
def api_chat_send(session_id):
    data = request.json or {}
    content = data.get("content", "").strip()
    if not content:
        return jsonify({"error": "消息不能为空"}), 400

    engine = _get_chat_engine()

    def generate():
        for event in engine.stream_reply(session_id, content):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
            'Connection': 'keep-alive',
        }
    )
```

**Step 2: Add "AI 助手" to navigation bar**

In `web/templates/base.html`, after the 雪球跟踪 nav link, add:

```html
<a href="/chat" class="px-3 py-2 rounded-md text-sm font-medium {% if request.endpoint == 'chat_page' %}text-blue-600 bg-blue-50{% else %}text-gray-600 hover:text-gray-900 hover:bg-gray-50{% endif %}">
    AI 助手
</a>
```

**Step 3: Run all tests**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 4: Commit**

```bash
git add web/app.py web/templates/base.html
git commit -m "feat(chat): add chat API routes and navigation link"
```

---

### Task 7: 雪球页面 UI 改造（用户选择器 + 爬取控制）

**Files:**
- Modify: `web/templates/xueqiu.html`

**Step 1: Rewrite xueqiu.html with user selector and sync controls**

Replace the top section of the Alpine.js data model to add:
- `users`, `selectedUserId`, `showAddUser`, `newUserId`, `newNickname` state
- `maxPages` state (default 5)
- `showSyncPanel` state
- `loadUsers()`, `addUser()`, `selectUser()` methods

Replace the header HTML to include:
- User dropdown selector with `<select>` bound to `selectedUserId`
- "+" button that opens add-user modal
- Sync button that opens a dropdown panel with `maxPages` input
- Progress bar during sync

Update `loadPosts()` to pass `user_id` parameter.
Update `startSync()` to pass `user_id` and `max_pages`.

The full template changes are extensive — implement by modifying the existing `xueqiu.html` in place. Key changes:

1. Add user state variables to `xueqiuApp()`:
```javascript
users: [],
selectedUserId: null,
showAddUser: false,
newUserId: '',
newNickname: '',
maxPages: 5,
showSyncPanel: false,
```

2. Add methods:
```javascript
async loadUsers() {
    const resp = await fetch('/api/xueqiu/users');
    const data = await resp.json();
    this.users = data.users || [];
},
async addUser() {
    const uid = parseInt(this.newUserId);
    if (!uid || !this.newNickname.trim()) return;
    await fetch('/api/xueqiu/users', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({user_id: uid, nickname: this.newNickname.trim()})
    });
    this.showAddUser = false;
    this.newUserId = '';
    this.newNickname = '';
    await this.loadUsers();
    this.selectedUserId = uid;
    this.resetAndLoad();
},
selectUser(uid) {
    this.selectedUserId = uid;
    this.resetAndLoad();
},
```

3. Update `init()` to call `loadUsers()` first
4. Update `loadPosts()` to include `user_id` param
5. Update `startSync()` to send `user_id` and `max_pages`

**Step 2: Verify manually**

Start the app and verify:
- User selector appears in header
- Can add a new user
- Can switch between users
- Sync panel shows max_pages input
- Progress displays during sync

**Step 3: Commit**

```bash
git add web/templates/xueqiu.html
git commit -m "feat(xueqiu): add user selector, sync controls, and batch size config"
```

---

### Task 8: AI 聊天页面

**Files:**
- Create: `web/templates/chat.html`

**Step 1: Create chat.html**

Create `web/templates/chat.html` extending `base.html`. The page uses Alpine.js with:

Layout: Left sidebar (session list) + Right main area (messages + input)

Alpine.js state:
```javascript
sessions: [],
currentSessionId: null,
messages: [],
inputText: '',
loading: false,
streaming: false,
```

Key features:
- Left sidebar: session list with "新对话" button, click to switch, delete button
- Message area: renders user/assistant/thinking messages
- Thinking messages: collapsible with click-to-expand
- Assistant messages: rendered as Markdown via `marked.parse()` + `DOMPurify.sanitize()`
- Input: textarea with Enter=send, Shift+Enter=newline
- SSE streaming: `fetch` POST with `ReadableStream` reader, parse `data:` lines
- Auto-scroll to bottom during streaming

SSE reading pattern:
```javascript
async sendMessage() {
    if (!this.inputText.trim() || this.streaming) return;
    const text = this.inputText.trim();
    this.inputText = '';
    this.messages.push({role: 'user', content: text});
    this.streaming = true;

    // Create session if needed
    if (!this.currentSessionId) {
        const resp = await fetch('/api/chat/sessions', {method: 'POST'});
        const session = await resp.json();
        this.currentSessionId = session.id;
        this.sessions.unshift(session);
    }

    // SSE stream
    const resp = await fetch(`/api/chat/sessions/${this.currentSessionId}/messages`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({content: text})
    });

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let thinkingBuf = '';
    let contentBuf = '';
    let thinkingMsg = null;
    let contentMsg = null;

    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        const lines = decoder.decode(value, {stream: true}).split('\n');
        for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const event = JSON.parse(line.slice(6));
            if (event.type === 'thinking') {
                thinkingBuf += event.content;
                if (!thinkingMsg) {
                    thinkingMsg = {role: 'thinking', content: thinkingBuf};
                    this.messages.push(thinkingMsg);
                } else {
                    thinkingMsg.content = thinkingBuf;
                }
            } else if (event.type === 'content') {
                contentBuf += event.content;
                if (!contentMsg) {
                    contentMsg = {role: 'assistant', content: contentBuf};
                    this.messages.push(contentMsg);
                } else {
                    contentMsg.content = contentBuf;
                }
            } else if (event.type === 'done') {
                if (event.session_title) {
                    const s = this.sessions.find(s => s.id === this.currentSessionId);
                    if (s) s.title = event.session_title;
                }
            } else if (event.type === 'error') {
                this.messages.push({role: 'error', content: event.content});
            }
        }
    }
    this.streaming = false;
}
```

**Step 2: Verify manually**

Start the app and verify:
- Chat page loads at /chat
- Can create new session
- Can send message and see streaming response
- Thinking process shows collapsed
- Markdown renders correctly
- Session list updates

**Step 3: Commit**

```bash
git add web/templates/chat.html
git commit -m "feat(chat): add AI chat page with SSE streaming and session management"
```

---

### Task 9: 集成测试 + 最终验证

**Files:**
- All modified files

**Step 1: Run full test suite**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: All tests PASS

**Step 2: Manual smoke test checklist**

1. 雪球页面：用户选择器显示、添加用户、切换用户筛选帖子
2. 雪球同步：设置页数、启动同步、进度展示
3. AI 聊天：创建会话、发送消息、流式响应、思考过程折叠
4. AI 聊天：切换会话、删除会话、历史消息加载
5. 导航栏：AI 助手链接正常

**Step 3: Final commit**

```bash
git add -A
git commit -m "feat: xueqiu v2 multi-user + AI chat assistant"
```
