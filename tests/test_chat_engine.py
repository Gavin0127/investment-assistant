"""Tests for ChatEngine."""

from unittest.mock import MagicMock
import pytest


@pytest.fixture()
def chat_db(tmp_path):
    from core.chat import ChatDB
    return ChatDB(str(tmp_path / "chat.db"))


@pytest.fixture()
def xueqiu_db(tmp_path):
    from core.xueqiu_db import XueqiuDB
    db = XueqiuDB(str(tmp_path / "xueqiu.db"))
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
        assert "投资研究" in messages[0]["content"]

    def test_build_context_includes_relevant_posts(self, engine, chat_db):
        sid = chat_db.create_session()
        messages = engine._build_messages(sid, "总结逸修1的观点")
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
