"""AI 聊天引擎：ChatDB（存储）+ ChatEngine（流式对话）"""

import contextlib
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


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


# Reasoning effort 后缀映射：模型名中包含 -<effort> 后缀时，
# 拆分为真实模型名 + reasoning_effort 参数
_REASONING_EFFORTS = {"none", "low", "medium", "high", "xhigh"}


def _parse_model_effort(model: str) -> tuple[str, str | None]:
    """从模型名中解析 reasoning effort 后缀。

    例如 'gpt-5.4-xhigh' → ('gpt-5.4', 'xhigh')
         'gpt-5.4'       → ('gpt-5.4', None)
    """
    for effort in _REASONING_EFFORTS:
        suffix = f"-{effort}"
        if model.endswith(suffix):
            return model[: -len(suffix)], effort
    return model, None


class ChatEngine:
    """AI 聊天引擎，支持 SSE 流式输出。"""

    def __init__(self, llm_client, xueqiu_db, chat_db: ChatDB, storage=None):
        self.llm = llm_client
        self.xueqiu_db = xueqiu_db
        self.chat_db = chat_db
        self.storage = storage

    def stream_reply(self, session_id: str, user_message: str, model: str | None = None):
        """生成器，yield SSE 事件 dict。model 可覆盖默认模型。"""
        self.chat_db.add_message(session_id, "user", user_message)
        messages = self._build_messages(session_id, user_message)
        raw_model = model or self.llm.model
        use_model, effort = _parse_model_effort(raw_model)

        thinking_buf = []
        content_buf = []
        try:
            create_kwargs = dict(
                model=use_model,
                messages=messages,
                stream=True,
                timeout=300,
            )
            if effort:
                create_kwargs["reasoning_effort"] = effort
            stream = self.llm.client.chat.completions.create(**create_kwargs)
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    thinking_buf.append(reasoning)
                    yield {"type": "thinking", "content": reasoning}

                text = delta.content
                if text:
                    content_buf.append(text)
                    yield {"type": "content", "content": text}

        except Exception as e:
            logger.error("ChatEngine stream error: %s", e)
            yield {"type": "error", "content": str(e)}
            return

        if thinking_buf:
            self.chat_db.add_message(session_id, "thinking", "".join(thinking_buf))
        if content_buf:
            self.chat_db.add_message(session_id, "assistant", "".join(content_buf))

        all_msgs = self.chat_db.get_messages(session_id)
        user_msgs = [m for m in all_msgs if m["role"] == "user"]
        session_title = None
        if len(user_msgs) == 1:
            session_title = self._generate_title(user_message, "".join(content_buf))

        yield {"type": "done", "session_title": session_title}

    def _build_messages(self, session_id: str, user_message: str) -> list[dict]:
        context_parts = [_SYSTEM_PROMPT]

        posts_context = self._search_relevant_posts(user_message)
        if posts_context:
            context_parts.append("\n--- 雪球帖子 ---\n")
            context_parts.append(posts_context)

        if self.storage:
            extra = self._load_system_context(user_message)
            if extra:
                context_parts.append("\n--- 系统数据 ---\n")
                context_parts.append(extra)

        messages = [{"role": "system", "content": "".join(context_parts)}]

        history = self.chat_db.get_messages(session_id)
        for msg in history:
            if msg["role"] in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _search_relevant_posts(self, query: str) -> str:
        parts = []
        users = self.xueqiu_db.list_users()
        target_user_id = None
        for u in users:
            if u["nickname"] in query:
                target_user_id = u["user_id"]
                break

        if target_user_id:
            posts, _ = self.xueqiu_db.list_posts(user_id=target_user_id, per_page=500)
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
            posts, _ = self.xueqiu_db.list_posts(query=query, per_page=50)
            for p in posts:
                desc = p.get("description") or ""
                title = p.get("title") or ""
                parts.append(f"{title}\n{desc}\n" if title else f"{desc}\n")

        return "\n".join(parts) if parts else ""

    def _load_system_context(self, query: str) -> str:
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
