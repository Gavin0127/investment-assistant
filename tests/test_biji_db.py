"""Tests for BijiDB."""

from core.biji_db import BijiDB


def test_save_note_and_asset(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        {
            "note_id": "n1",
            "title": "第一篇",
            "summary": "摘要",
            "raw_content": "<p>Hello</p>",
            "markdown_content": "Hello",
            "source_url": "https://www.biji.com/note/n1",
            "created_at": "2026-03-24 10:00:00",
            "updated_at": "2026-03-24 10:00:00",
            "content_hash": "abc",
            "missing_from_remote": 0,
        }
    )
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

    note = db.get_note("n1")
    assets = db.list_assets("n1")

    assert note["title"] == "第一篇"
    assert assets[0]["filename"] == "001.png"


def test_sync_state_round_trip(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.set_sync_state("initial_full_sync_done", "1")

    assert db.get_sync_state("initial_full_sync_done") == "1"
