"""Tests for BijiSyncService."""

from pathlib import Path

from core.biji_db import BijiDB


class FakeClient:
    def __init__(self, detail_overrides=None, download_fail_urls=None):
        self.detail_overrides = detail_overrides or {}
        self.download_fail_urls = set(download_fail_urls or [])
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
        if url in self.download_fail_urls:
            raise RuntimeError("download failed")
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"image-bytes")


class RepeatingPageClient(FakeClient):
    def list_notes(self, page: int, page_size: int) -> list[dict]:
        return super().list_notes(page=1, page_size=page_size)


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


def test_failed_export_does_not_mark_note_as_synced_and_next_run_retries(tmp_path, monkeypatch):
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

    def fail_once(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_write_raw_snapshot", fail_once)
    first = service.sync_once()

    assert first["created"] == 0
    assert first["updated"] == 0
    assert first["failed"] == 1
    assert db.get_note("n1") is None

    monkeypatch.undo()
    second = service.sync_once()

    assert second["created"] == 1
    assert second["skipped"] == 0
    assert client.detail_calls == 2


def test_sync_marks_missing_notes_when_remote_no_longer_returns_them(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        {
            "note_id": "n2",
            "title": "旧笔记",
            "summary": "旧摘要",
            "raw_content": "old",
            "markdown_content": "old",
            "source_url": "https://www.biji.com/note/n2",
            "created_at": "2026-03-23 10:00:00",
            "updated_at": "2026-03-23 10:00:00",
            "content_hash": "old-hash",
            "missing_from_remote": 0,
        }
    )
    client = FakeClient()
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()

    missing_note = db.get_note("n2")
    assert missing_note["missing_from_remote"] == 1
    assert db.get_note("n1")["missing_from_remote"] == 0


def test_failed_asset_download_keeps_remote_url_in_markdown(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient(download_fail_urls={"https://img.example.com/a.png"})
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()
    markdown_path = tmp_path / "biji_markdown" / "n1" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert result["created"] == 1
    assert "https://img.example.com/a.png" in markdown
    assert assets[0]["local_path"] is None
    assert assets[0]["download_status"] == "failed"


def test_empty_raw_content_still_exports_stable_markdown_file(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient(detail_overrides={"raw_content": "", "assets": []})
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()
    markdown_path = tmp_path / "biji_markdown" / "n1" / "index.md"
    content = markdown_path.read_text(encoding="utf-8")

    assert result["created"] == 1
    assert markdown_path.exists()
    assert content.startswith("---\n")
    assert 'title: "第一篇"' in content


def test_incomplete_scan_does_not_mark_existing_notes_missing(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        {
            "note_id": "n2",
            "title": "旧笔记",
            "summary": "旧摘要",
            "raw_content": "old",
            "markdown_content": "old",
            "source_url": "https://www.biji.com/note/n2",
            "created_at": "2026-03-23 10:00:00",
            "updated_at": "2026-03-23 10:00:00",
            "content_hash": "old-hash",
            "missing_from_remote": 0,
        }
    )
    client = RepeatingPageClient()
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()

    assert db.get_note("n2")["missing_from_remote"] == 0


def test_missing_note_reappearing_with_same_updated_at_is_refetched_and_unmarked(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        {
            "note_id": "n1",
            "title": "旧标题",
            "summary": "旧摘要",
            "raw_content": "old",
            "markdown_content": "old",
            "source_url": "https://www.biji.com/note/n1",
            "created_at": "2026-03-24 10:00:00",
            "updated_at": "2026-03-24 10:00:00",
            "content_hash": "old-hash",
            "missing_from_remote": 1,
        }
    )
    client = FakeClient(
        detail_overrides={
            "raw_content": "<p>new body</p>",
            "summary": "新摘要",
            "updated_at": "2026-03-24 10:00:00",
        }
    )
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()
    note = db.get_note("n1")

    assert result["updated"] == 1
    assert client.detail_calls == 1
    assert note["missing_from_remote"] == 0
    assert note["summary"] == "新摘要"
