from pathlib import Path

import pytest

from scripts.sync_biji import build_parser, main


def test_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])

    assert args.page_size == 50
    assert args.full is False
    assert args.no_images is False
    assert args.verbose is False
    assert args.base_dir is None


def test_main_wires_dependencies_and_prints_summary(monkeypatch, tmp_path, capsys):
    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "api_base": "https://notes-api.biji.com",
                "page_size": 50,
                "download_images": True,
            }

        def get_biji_token(self):
            return "secret-token"

    class FakeClient:
        def __init__(self, api_base, bearer_token):
            self.api_base = api_base
            self.bearer_token = bearer_token

    class FakeDB:
        def __init__(self, db_path):
            self.db_path = db_path

    class FakeSyncService:
        def __init__(self, client, db, markdown_root, raw_root, page_size):
            self.client = client
            self.db = db
            self.markdown_root = markdown_root
            self.raw_root = raw_root
            self.page_size = page_size

        def sync_once(self):
            return {"created": 1, "updated": 2, "skipped": 3, "failed": 0}

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiClient", FakeClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", FakeDB)
    monkeypatch.setattr("scripts.sync_biji.BijiSyncService", FakeSyncService)

    exit_code = main(["--base-dir", str(tmp_path), "--page-size", "20", "--no-images"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "created=1" in captured.out
    assert "updated=2" in captured.out
    assert "skipped=3" in captured.out
    assert "failed=0" in captured.out


def test_main_exits_when_token_missing(monkeypatch, tmp_path, capsys):
    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "api_base": "https://notes-api.biji.com",
                "page_size": 50,
                "download_images": True,
            }

        def get_biji_token(self):
            return None

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)

    exit_code = main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "bearer token" in captured.err.lower()
