"""Tests for BijiSyncService."""

from pathlib import Path

from core.biji_db import BijiDB


class FakeClient:
    def __init__(self, detail_overrides=None):
        self.detail_overrides = detail_overrides or {}
        self.detail_calls = 0
        self.download_calls = 0

    def list_notes(self, page: int, page_size: int) -> list[dict]:
        if page > 1:
            return []
        return [
            {
                "note_id": "n1",
                "title": "第一篇",
                "summary": "摘要",
                "source_url": "https://www.biji.com/note/n1",
                "created_at": "2026-03-24 10:00:00",
                "updated_at": "2026-03-24 10:00:00",
            }
        ]

    def get_note_detail(self, note_id: str) -> dict:
        self.detail_calls += 1
        detail = {
            "note_id": note_id,
            "title": "第一篇",
            "summary": "摘要",
            "source_url": "https://www.biji.com/note/n1",
            "created_at": "2026-03-24 10:00:00",
            "updated_at": "2026-03-24 10:00:00",
            "raw_content": "<p>Hello <img src=\"https://img.example.com/a.png\" /></p>",
            "assets": [
                {
                    "asset_url": "https://img.example.com/a.png",
                    "asset_type": "image",
                    "mime_type": "image/png",
                    "title": "",
                }
            ],
        }
        detail.update(self.detail_overrides)
        return detail

    def download_asset(self, url: str, dest_path: str | Path):
        self.download_calls += 1
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"image-bytes")


def test_first_sync_creates_db_row_and_markdown(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient()
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()

    assert result["created"] == 1
    assert result["updated"] == 0
    note = db.get_note("n1")
    assert note["title"] == "第一篇"
    assert note["markdown_content"]
    assert (tmp_path / "biji_markdown" / "n1" / "index.md").exists()


def test_second_sync_skips_unchanged_note(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient()
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    first = service.sync_once()
    second = service.sync_once()

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["updated"] == 0
    assert second["skipped"] == 1
    assert client.detail_calls == 1


def test_sync_replaces_remote_image_urls_with_local_relative_paths(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient()
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()

    markdown_path = tmp_path / "biji_markdown" / "n1" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert "https://img.example.com/a.png" not in markdown
    assert "assets/001.png" in markdown
    assert assets[0]["local_path"] == "assets/001.png"
    assert (tmp_path / "biji_markdown" / "n1" / "assets" / "001.png").exists()
