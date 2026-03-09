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
