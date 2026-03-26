"""Tests for BijiDB."""

import time

from core.biji_db import BijiDB


def _make_note(note_id: str, **overrides):
    note = {
        "note_id": note_id,
        "title": f"title-{note_id}",
        "summary": f"summary-{note_id}",
        "content_mode": "unknown",
        "original_content": "",
        "ai_summary_content": "",
        "display_content": "",
        "content_source": "api_detail",
        "raw_content": f"<p>{note_id}</p>",
        "markdown_content": note_id,
        "source_url": f"https://www.biji.com/note/{note_id}",
        "created_at": "2026-03-24 10:00:00",
        "updated_at": "2026-03-24 10:00:00",
        "content_hash": f"hash-{note_id}",
        "missing_from_remote": 0,
        "export_dir_name": f"title-{note_id}",
    }
    note.update(overrides)
    return note


def test_upsert_note_and_get_note(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1", title="第一篇"))

    note = db.get_note("n1")

    assert note["title"] == "第一篇"
    assert note["content_hash"] == "hash-n1"


def test_upsert_note_updates_existing_row_and_parses_missing_flag(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1", title="旧标题", missing_from_remote=1))
    db.upsert_note(
        _make_note(
            "n1",
            title="新标题",
            summary="新摘要",
            updated_at="2026-03-24 12:00:00",
            missing_from_remote="0",
        )
    )

    note = db.get_note("n1")

    assert note["title"] == "新标题"
    assert note["summary"] == "新摘要"
    assert note["missing_from_remote"] == 0


def test_upsert_note_persists_content_mode_and_export_dir_name(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        _make_note(
            "n1",
            content_mode="ai_note",
            original_content="逐字原文",
            ai_summary_content="AI 摘要",
            display_content="## 原始内容\n\n逐字原文",
            content_source="mixed",
            export_dir_name="字节游戏分析",
        )
    )

    note = db.get_note("n1")

    assert note["content_mode"] == "ai_note"
    assert note["original_content"] == "逐字原文"
    assert note["ai_summary_content"] == "AI 摘要"
    assert note["display_content"] == "## 原始内容\n\n逐字原文"
    assert note["content_source"] == "mixed"
    assert note["export_dir_name"] == "字节游戏分析"


def test_init_schema_creates_notes_fts_and_note_chunks(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))

    with db._get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }

    assert "notes_fts" in tables
    assert "note_chunks" in tables


def test_upsert_chunk_and_search_fts(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        _make_note(
            "n1",
            title="英伟达护城河分析",
            original_content="原始内容",
            ai_summary_content="Token经济与加速计算",
            display_content="Token经济与护城河",
        )
    )
    db.upsert_chunk(
        {
            "chunk_id": "n1-0001",
            "note_id": "n1",
            "chunk_index": 1,
            "section_type": "ai_summary_content",
            "text": "英伟达的Token经济和护城河分析",
            "token_estimate": 42,
            "char_start": 0,
            "char_end": 16,
            "content_hash": "chunk-hash",
            "markdown_path": "/tmp/英伟达.md",
        }
    )

    chunks = db.list_chunks("n1")
    hits = db.search_notes_fts("英伟达")

    assert chunks[0]["chunk_id"] == "n1-0001"
    assert hits[0]["note_id"] == "n1"


def test_existing_notes_are_backfilled_into_fts_on_reopen(tmp_path):
    db_path = tmp_path / "biji.db"
    with BijiDB(str(db_path))._get_conn() as conn:
        conn.executescript(
            """
            DROP TABLE IF EXISTS notes_fts;
            DROP TABLE IF EXISTS note_chunks;
            DROP TABLE IF EXISTS sync_state;
            DROP TABLE IF EXISTS api_snapshots;
            DROP TABLE IF EXISTS note_assets;
            DROP TABLE IF EXISTS notes;
            CREATE TABLE notes (
                note_id TEXT PRIMARY KEY,
                title TEXT,
                summary TEXT,
                content_mode TEXT,
                original_content TEXT,
                ai_summary_content TEXT,
                display_content TEXT,
                content_source TEXT,
                raw_content TEXT,
                markdown_content TEXT,
                source_url TEXT,
                created_at TEXT,
                updated_at TEXT,
                saved_at INTEGER,
                content_hash TEXT,
                missing_from_remote INTEGER,
                last_exported_at INTEGER,
                export_dir_name TEXT
            );
            """
        )
        conn.execute(
            """
            INSERT INTO notes (
                note_id, title, summary, content_mode, original_content, ai_summary_content,
                display_content, content_source, raw_content, markdown_content, source_url,
                created_at, updated_at, saved_at, content_hash, missing_from_remote,
                last_exported_at, export_dir_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "n1", "英伟达分析", "摘要", "ai_note", "原始内容", "Token经济",
                "Token经济与护城河", "mixed", "raw", "md", "https://www.biji.com/note/n1",
                "2026-03-24 10:00:00", "2026-03-24 10:00:00", 0, "hash-n1", 0, 0, "英伟达分析",
            ),
        )
        conn.commit()

    db = BijiDB(str(db_path))
    hits = db.search_notes_fts("英伟达")

    assert hits[0]["note_id"] == "n1"


def test_search_notes_fts_treats_special_characters_as_literal_text(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        _make_note(
            "n1",
            title="C++ 并发分析",
            display_content="C++ 与护城河",
        )
    )

    hits = db.search_notes_fts("C++")

    assert hits[0]["note_id"] == "n1"


def test_replace_chunks_for_note_validates_note_id_consistency(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1"))

    try:
        db.replace_chunks_for_note(
            "n1",
            [
                {
                    "chunk_id": "n2-0001",
                    "note_id": "n2",
                    "chunk_index": 1,
                    "section_type": "ai_summary_content",
                    "text": "wrong note",
                    "token_estimate": 2,
                    "char_start": 0,
                    "char_end": 10,
                    "content_hash": "chunk-hash",
                    "markdown_path": "/tmp/n2.md",
                }
            ],
        )
    except ValueError as exc:
        assert "note_id mismatch" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_list_notes_orders_by_updated_at_desc_then_note_id_desc(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1", updated_at="2026-03-24 09:00:00"))
    db.upsert_note(_make_note("n2", updated_at="2026-03-24 12:00:00"))
    db.upsert_note(_make_note("n3", updated_at="2026-03-24 12:00:00"))

    notes = db.list_notes()

    assert [note["note_id"] for note in notes] == ["n3", "n2", "n1"]


def test_upsert_asset_and_list_assets(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1"))
    db.upsert_asset(
        {
            "note_id": "n1",
            "asset_url": "https://img.example.com/a.png",
            "asset_type": "image",
            "filename": "001.png",
            "local_path": "biji_markdown/n1/assets/001.png",
            "download_status": "done",
        }
    )

    assets = db.list_assets("n1")

    assert assets[0]["filename"] == "001.png"


def test_upsert_asset_updates_existing_row_without_reordering_list(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(_make_note("n1"))
    db.upsert_asset(
        {
            "note_id": "n1",
            "asset_url": "https://img.example.com/a.png",
            "asset_type": "image",
            "filename": "001.png",
            "local_path": "biji_markdown/n1/assets/001.png",
            "download_status": "queued",
        }
    )
    db.upsert_asset(
        {
            "note_id": "n1",
            "asset_url": "https://img.example.com/b.png",
            "asset_type": "image",
            "filename": "002.png",
            "local_path": "biji_markdown/n1/assets/002.png",
            "download_status": "done",
        }
    )
    time.sleep(1.1)
    db.upsert_asset(
        {
            "note_id": "n1",
            "asset_url": "https://img.example.com/a.png",
            "asset_type": "image",
            "filename": "001.png",
            "local_path": "biji_markdown/n1/assets/001.png",
            "download_status": "done",
        }
    )

    assets = db.list_assets("n1")

    assert [asset["filename"] for asset in assets] == ["001.png", "002.png"]
    assert assets[0]["download_status"] == "done"


def test_sync_state_round_trip_and_overwrite(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.set_sync_state("initial_full_sync_done", "1")
    db.set_sync_state("initial_full_sync_done", "0")

    assert db.get_sync_state("initial_full_sync_done") == "0"
