from pathlib import Path

import pytest

from scripts.sync_biji import build_parser, main


def test_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])

    assert args.page_size is None
    assert args.full is False
    assert args.login is False
    assert args.no_images is False
    assert args.verbose is False
    assert args.base_dir is None


def test_main_wires_browser_client_and_prints_summary(monkeypatch, tmp_path, capsys):
    state = {}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)
            state["storage_base_dir"] = self.base_dir

        def get_biji_config(self):
            return {
                "auth_mode": "browser_session",
                "api_base": "https://notes-api.biji.com",
                "browser_profile_dir": str(self.base_dir / "data" / "biji_browser"),
                "page_size": 77,
                "download_images": True,
            }

        def get_biji_token(self):
            return "secret-token"

    class FakeBrowserClient:
        def __init__(self, api_base, profile_dir, headless=True):
            self.api_base = api_base
            self.profile_dir = profile_dir
            self.headless = headless
            self.download_asset = lambda *_args, **_kwargs: None
            state["client"] = self

        def close(self):
            state["closed"] = True

    class FakeDB:
        def __init__(self, db_path):
            self.db_path = db_path
            state["db_path"] = db_path

    class FakeSyncService:
        def __init__(self, client, db, markdown_root, raw_root, page_size, download_images=True):
            self.client = client
            self.db = db
            self.markdown_root = markdown_root
            self.raw_root = raw_root
            self.page_size = page_size
            self.download_images = download_images
            state["service"] = self

        def sync_once(self):
            return {"created": 1, "updated": 2, "skipped": 3, "failed": 0}

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiBrowserClient", FakeBrowserClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", FakeDB)
    monkeypatch.setattr("scripts.sync_biji.BijiSyncService", FakeSyncService)

    exit_code = main(["--base-dir", str(tmp_path), "--page-size", "20", "--no-images"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "created=1" in captured.out
    assert "updated=2" in captured.out
    assert "skipped=3" in captured.out
    assert "failed=0" in captured.out
    assert state["storage_base_dir"] == tmp_path
    assert state["client"].profile_dir == str(tmp_path / "data" / "biji_browser")
    assert state["client"].headless is True
    assert state["closed"] is True
    assert state["service"].page_size == 20
    assert state["service"].download_images is False
    assert state["service"].markdown_root == str(tmp_path / "data" / "biji_markdown")
    assert state["service"].raw_root == str(tmp_path / "data" / "biji_raw")
    assert state["db_path"] == str(tmp_path / "data" / "biji_notes.db")


def test_main_uses_config_page_size_when_flag_is_omitted(monkeypatch, tmp_path, capsys):
    captured = {}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "auth_mode": "browser_session",
                "api_base": "https://notes-api.biji.com",
                "browser_profile_dir": str(self.base_dir / "data" / "biji_browser"),
                "page_size": 33,
                "download_images": True,
            }

        def get_biji_token(self):
            return "secret-token"

    class FakeBrowserClient:
        def __init__(self, api_base, profile_dir, headless=True):
            self.download_asset = lambda *_args, **_kwargs: None

        def close(self):
            return None

    class FakeDB:
        def __init__(self, db_path):
            self.db_path = db_path

    class FakeSyncService:
        def __init__(self, client, db, markdown_root, raw_root, page_size, download_images=True):
            captured["page_size"] = page_size
            captured["download_images"] = download_images

        def sync_once(self):
            return {"created": 0, "updated": 0, "skipped": 1, "failed": 0}

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiBrowserClient", FakeBrowserClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", FakeDB)
    monkeypatch.setattr("scripts.sync_biji.BijiSyncService", FakeSyncService)

    exit_code = main(["--base-dir", str(tmp_path)])

    captured_output = capsys.readouterr()
    assert exit_code == 0
    assert captured["page_size"] == 33
    assert captured["download_images"] is True
    assert "skipped=1" in captured_output.out


def test_main_exits_when_token_missing_in_bearer_mode(monkeypatch, tmp_path, capsys):
    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "auth_mode": "bearer",
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


def test_main_login_uses_browser_client_without_running_sync(monkeypatch, tmp_path, capsys):
    state = {"login_calls": 0}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "auth_mode": "browser_session",
                "api_base": "https://notes-api.biji.com",
                "browser_profile_dir": str(self.base_dir / "data" / "biji_browser"),
                "page_size": 50,
                "download_images": True,
            }

    class FakeBrowserClient:
        def __init__(self, api_base, profile_dir, headless=True):
            state["headless"] = headless
            state["profile_dir"] = profile_dir

        def login(self):
            state["login_calls"] += 1

        def close(self):
            state["closed"] = True

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiBrowserClient", FakeBrowserClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", lambda *_args, **_kwargs: pytest.fail("db should not be created"))
    monkeypatch.setattr(
        "scripts.sync_biji.BijiSyncService",
        lambda *_args, **_kwargs: pytest.fail("sync service should not be created"),
    )

    exit_code = main(["--base-dir", str(tmp_path), "--login"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert state["login_calls"] == 1
    assert state["headless"] is False
    assert state["profile_dir"] == str(tmp_path / "data" / "biji_browser")
    assert state["closed"] is True
    assert "login" in captured.out.lower()


def test_main_uses_bearer_client_when_auth_mode_is_bearer(monkeypatch, tmp_path, capsys):
    state = {}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "auth_mode": "bearer",
                "api_base": "https://notes-api.biji.com",
                "page_size": 50,
                "download_images": True,
            }

        def get_biji_token(self):
            return "secret-token"

    class FakeClient:
        def __init__(self, api_base, bearer_token):
            state["api_base"] = api_base
            state["bearer_token"] = bearer_token
            self.download_asset = lambda *_args, **_kwargs: None

    class FakeDB:
        def __init__(self, db_path):
            self.db_path = db_path

    class FakeSyncService:
        def __init__(self, client, db, markdown_root, raw_root, page_size, download_images=True):
            state["page_size"] = page_size

        def sync_once(self):
            return {"created": 0, "updated": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiClient", FakeClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", FakeDB)
    monkeypatch.setattr("scripts.sync_biji.BijiSyncService", FakeSyncService)

    exit_code = main(["--base-dir", str(tmp_path)])

    capsys.readouterr()
    assert exit_code == 0
    assert state["api_base"] == "https://notes-api.biji.com"
    assert state["bearer_token"] == "secret-token"
    assert state["page_size"] == 50


def test_main_exits_cleanly_on_unexpected_error(monkeypatch, tmp_path, capsys):
    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            raise RuntimeError("boom")

        def get_biji_token(self):
            return "secret-token"

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)

    exit_code = main(["--base-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "boom" in captured.err.lower()
