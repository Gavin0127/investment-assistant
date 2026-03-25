"""Tests for browser-session backed Biji client."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests


_LIST_FIXTURE = Path("tests/fixtures/biji/list_page_1.json")
_DETAIL_FIXTURE = Path("tests/fixtures/biji/note_detail_sample.json")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"file-bytes"):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

    def json(self):
        return self._payload


def test_browser_client_parses_list_fixture_via_browser_fetch(monkeypatch, tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "biji_browser"),
    )
    monkeypatch.setattr(
        client,
        "_browser_fetch_response",
        lambda url, params=None: _FakeResponse(status_code=200, payload=_load_json(_LIST_FIXTURE)),
    )

    notes, meta = client.list_notes_page(page=1, page_size=5)

    assert len(notes) == 3
    assert notes[0]["note_id"] == "1905199764681666160"
    assert meta == {"has_more": True, "total_items": 551}


def test_browser_client_parses_detail_fixture_via_browser_fetch(monkeypatch, tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "biji_browser"),
    )
    monkeypatch.setattr(
        client,
        "_browser_fetch_response",
        lambda url, params=None: _FakeResponse(status_code=200, payload=_load_json(_DETAIL_FIXTURE)),
    )

    detail = client.get_note_detail("1905199764681666160")

    assert detail["note_id"] == "1905199764681666160"
    assert detail["title"] == "AI时代小团队效率提升实践与Agent发展趋势分享"


def test_login_waits_until_user_info_is_ready(tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "biji_browser"),
        headless=False,
    )
    client._page = MagicMock()
    client._page.goto = MagicMock()
    client._page.wait_for_timeout = MagicMock()
    client._ensure_browser = MagicMock(return_value=client._page)
    client._browser_fetch_response = MagicMock(
        side_effect=[
            _FakeResponse(status_code=403, payload={"message": "LoginRequired"}),
            _FakeResponse(status_code=200, payload={"c": {"uid": "123"}}),
        ]
    )

    client.login(timeout_seconds=2, poll_interval_ms=1)

    client._page.goto.assert_called_once()
    assert client._browser_fetch_response.call_count == 2
    client._page.wait_for_timeout.assert_called_once_with(1)


def test_download_asset_uses_browser_cookies_for_requests(monkeypatch, tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    session = requests.Session()
    session.get = MagicMock(return_value=_FakeResponse(status_code=200, content=b"asset-bytes"))

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "biji_browser"),
        session=session,
    )
    client._context = MagicMock()
    client._context.cookies.return_value = [
        {
            "name": "sessionid",
            "value": "cookie-value",
            "domain": ".biji.com",
            "path": "/",
        }
    ]

    dest = tmp_path / "audio.mp3"
    with patch("core.biji_browser_client.time.sleep"):
        client.download_asset("https://assets.example.invalid/biji/audio-sample.mp3", dest)

    assert dest.read_bytes() == b"asset-bytes"
    assert session.cookies.get("sessionid", domain=".biji.com", path="/") == "cookie-value"
    called_headers = session.get.call_args.kwargs["headers"]
    assert "Authorization" not in called_headers


def test_download_asset_does_not_treat_non_web_api_url_as_json(monkeypatch, tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    session = requests.Session()
    session.get = MagicMock(return_value=_FakeResponse(status_code=200, content=b"binary-asset"))

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "biji_browser"),
        session=session,
    )
    client._context = MagicMock()
    client._context.cookies.return_value = []
    browser_fetch = MagicMock()
    monkeypatch.setattr(client, "_browser_fetch_response", browser_fetch)

    dest = tmp_path / "asset.bin"
    client.download_asset("https://notes-api.biji.com/files/export.bin", dest)

    assert dest.read_bytes() == b"binary-asset"
    browser_fetch.assert_not_called()
