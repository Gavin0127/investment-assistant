"""Tests for BijiSyncService."""

from pathlib import Path

from core.biji_db import BijiDB


class FakeClient:
    def __init__(self, detail_overrides=None, download_fail_urls=None, page_responses=None):
        self.detail_overrides = detail_overrides or {}
        self.download_fail_urls = set(download_fail_urls or [])
        self.page_responses = page_responses or {
            1: {
                "notes": [
                    {
                        "note_id": "n1",
                        "title": "第一篇",
                        "summary": "摘要",
                        "source_url": "https://www.biji.com/note/n1",
                        "created_at": "2026-03-24 10:00:00",
                        "updated_at": "2026-03-24 10:00:00",
                    }
                ],
                "meta": {"has_more": False, "total_items": 1},
            }
        }
        self.detail_calls = 0
        self.download_calls = 0

    def list_notes(self, page: int, page_size: int) -> list[dict]:
        notes, _ = self.list_notes_page(page=page, page_size=page_size)
        return notes

    def list_notes_page(self, page: int, page_size: int) -> tuple[list[dict], dict]:
        page_data = self.page_responses.get(page)
        if page_data is None:
            return [], {"has_more": False, "total_items": 0}
        return list(page_data["notes"]), dict(page_data.get("meta") or {})

    def get_note_detail(self, note_id: str) -> dict:
        self.detail_calls += 1
        detail = {
            "note_id": note_id,
            "title": "第一篇",
            "summary": "摘要",
            "source_url": "https://www.biji.com/note/n1",
            "created_at": "2026-03-24 10:00:00",
            "updated_at": "2026-03-24 10:00:00",
            "raw_content": "<p>Hello <img src=\"https://get-notes.umiwi.com/a.png\" /></p>",
            "assets": [
                {
                    "asset_url": "https://get-notes.umiwi.com/a.png",
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

    def get_note_page_snapshot(self, note_id: str) -> dict:
        return {
            "note_url": f"https://www.biji.com/note/{note_id}/web",
            "html": "",
            "text": "",
        }


class RepeatingPageClient(FakeClient):
    def __init__(self, *args, **kwargs):
        super().__init__(
            *args,
            page_responses={
                1: {
                    "notes": [
                        {
                            "note_id": "n1",
                            "title": "第一篇",
                            "summary": "摘要",
                            "source_url": "https://www.biji.com/note/n1",
                            "created_at": "2026-03-24 10:00:00",
                            "updated_at": "2026-03-24 10:00:00",
                        }
                    ],
                    "meta": {"has_more": True, "total_items": 2},
                },
                2: {
                    "notes": [
                        {
                            "note_id": "n1",
                            "title": "第一篇",
                            "summary": "摘要",
                            "source_url": "https://www.biji.com/note/n1",
                            "created_at": "2026-03-24 10:00:00",
                            "updated_at": "2026-03-24 10:00:00",
                        }
                    ],
                    "meta": {"has_more": True, "total_items": 2},
                },
            },
            **kwargs,
        )


class CursorFakeClient(FakeClient):
    def __init__(self, cursor_responses, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cursor_responses = list(cursor_responses)
        self.cursor_calls = []

    def list_notes_batch(self, *, since_id: str, limit: int, sort: str = "edit_desc"):
        self.cursor_calls.append({"since_id": since_id, "limit": limit, "sort": sort})
        if not self.cursor_responses:
            return [], {"has_more": False, "total_items": 0}
        return self.cursor_responses.pop(0)


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
    assert (tmp_path / "biji_markdown" / "第一篇" / "index.md").exists()


def test_sync_exports_ai_note_with_title_directory_and_dual_sections(tmp_path):
    from core.biji_sync import BijiSyncService

    class PageAwareClient(FakeClient):
        def get_note_page_snapshot(self, note_id: str):
            return {
                "note_url": f"https://www.biji.com/note/{note_id}/web",
                "html": "<h2>原始内容</h2><div>逐字原文</div><h2>AI 总结</h2><div>整理摘要</div>",
                "text": "原始内容\n逐字原文\nAI 总结\n整理摘要",
            }

    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=PageAwareClient(detail_overrides={"assets": []}),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()

    assert result["created"] == 1
    note = db.get_note("n1")
    assert note["content_mode"] == "ai_note"
    assert note["original_content"] == "逐字原文"
    assert note["ai_summary_content"] == "整理摘要"
    assert note["export_dir_name"] == "第一篇"
    markdown_path = tmp_path / "biji_markdown" / "第一篇" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## 原始内容" in markdown
    assert "## AI 总结（需验证可信度）" in markdown
    assert not (tmp_path / "biji_markdown" / "n1").exists()


def test_sync_exports_native_note_without_ai_heading(tmp_path):
    from core.biji_sync import BijiSyncService

    class NativeClient(FakeClient):
        def get_note_page_snapshot(self, note_id: str):
            return {
                "note_url": f"https://www.biji.com/note/{note_id}/web",
                "html": "<article><p>这是原生正文</p></article>",
                "text": "这是原生正文",
            }

    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=NativeClient(detail_overrides={"raw_content": "这是原生正文", "assets": []}),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()

    note = db.get_note("n1")
    markdown = (tmp_path / "biji_markdown" / "第一篇" / "index.md").read_text(encoding="utf-8")
    assert note["content_mode"] == "native_note"
    assert "## 笔记正文" in markdown
    assert "AI 总结（需验证可信度）" not in markdown


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

    markdown_path = tmp_path / "biji_markdown" / "第一篇" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert "https://get-notes.umiwi.com/a.png" not in markdown
    assert "assets/001.png" in markdown
    assert assets[0]["local_path"] == "assets/001.png"
    assert (tmp_path / "biji_markdown" / "第一篇" / "assets" / "001.png").exists()


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
    client = FakeClient(download_fail_urls={"https://get-notes.umiwi.com/a.png"})
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()
    markdown_path = tmp_path / "biji_markdown" / "第一篇" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert result["created"] == 1
    assert "https://get-notes.umiwi.com/a.png" in markdown
    assert assets[0]["local_path"] is None
    assert assets[0]["download_status"] == "failed"


def test_external_asset_is_recorded_without_download(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient(
        detail_overrides={
            "raw_content": '<p>Ref <a href="https://xueqiu.com/123/456">雪球链接</a></p>',
            "assets": [
                {
                    "asset_url": "https://xueqiu.com/123/456",
                    "asset_type": "attachment",
                    "mime_type": "text/html",
                    "title": "雪球链接",
                }
            ],
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
    markdown = (tmp_path / "biji_markdown" / "第一篇" / "index.md").read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert result["created"] == 1
    assert client.download_calls == 0
    assert "https://xueqiu.com/123/456" in markdown
    assert assets[0]["asset_url"] == "https://xueqiu.com/123/456"
    assert assets[0]["local_path"] is None
    assert assets[0]["download_status"] == "external"


def test_no_images_only_skips_image_downloads(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = FakeClient(
        detail_overrides={
            "raw_content": (
                '<p>Hello <img src="https://get-notes.umiwi.com/a.png" /></p>'
                "\n<audio src=\"https://mediacdn.umiwi.com/audio.mp3\"></audio>"
            ),
            "assets": [
                {
                    "asset_url": "https://get-notes.umiwi.com/a.png",
                    "asset_type": "image",
                    "mime_type": "image/png",
                    "title": "",
                },
                {
                    "asset_url": "https://mediacdn.umiwi.com/audio.mp3",
                    "asset_type": "audio",
                    "mime_type": "audio/mpeg",
                    "title": "",
                },
            ],
        }
    )
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
        download_images=False,
    )

    service.sync_once()

    markdown = (tmp_path / "biji_markdown" / "第一篇" / "index.md").read_text(encoding="utf-8")
    assets = db.list_assets("n1")

    assert client.download_calls == 1
    assert "https://get-notes.umiwi.com/a.png" in markdown
    assert assets[0]["asset_type"] == "image"
    assert assets[0]["download_status"] == "skipped"
    assert assets[1]["asset_type"] == "audio"
    assert assets[1]["download_status"] == "done"
    assert assets[1]["local_path"] == "assets/002.mp3"
    assert (tmp_path / "biji_markdown" / "第一篇" / "assets" / "002.mp3").exists()


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
    markdown_path = tmp_path / "biji_markdown" / "第一篇" / "index.md"
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


def test_sync_uses_since_id_cursor_protocol_when_client_supports_it(tmp_path):
    from core.biji_sync import BijiSyncService

    db = BijiDB(str(tmp_path / "biji.db"))
    client = CursorFakeClient(
        cursor_responses=[
            (
                [
                    {
                        "note_id": "n1",
                        "title": "第一篇",
                        "summary": "摘要",
                        "source_url": "https://www.biji.com/note/n1",
                        "created_at": "2026-03-24 10:00:00",
                        "updated_at": "2026-03-24 10:00:00",
                    },
                    {
                        "note_id": "n2",
                        "title": "第二篇",
                        "summary": "摘要",
                        "source_url": "https://www.biji.com/note/n2",
                        "created_at": "2026-03-24 09:00:00",
                        "updated_at": "2026-03-24 09:00:00",
                    },
                ],
                {"has_more": True, "total_items": 553},
            ),
            (
                [
                    {
                        "note_id": "n3",
                        "title": "第三篇",
                        "summary": "摘要",
                        "source_url": "https://www.biji.com/note/n3",
                        "created_at": "2026-03-24 08:00:00",
                        "updated_at": "2026-03-24 08:00:00",
                    }
                ],
                {"has_more": False, "total_items": 553},
            ),
        ],
        detail_overrides={"assets": []},
    )
    service = BijiSyncService(
        client=client,
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=50,
    )

    result = service.sync_once()

    assert result == {"created": 3, "updated": 0, "skipped": 0, "failed": 0}
    assert client.cursor_calls == [
        {"since_id": "0", "limit": 50, "sort": "edit_desc"},
        {"since_id": "n2", "limit": 50, "sort": "edit_desc"},
    ]
    assert db.get_note("n3")["note_id"] == "n3"


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
