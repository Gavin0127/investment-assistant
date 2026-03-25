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
