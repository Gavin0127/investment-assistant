"""Tests for BijiClient."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


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

    def raise_for_status(self):
        return None


def test_build_headers_includes_bearer():
    from core.biji_client import BijiClient

    client = BijiClient(api_base="https://notes-api.biji.com", bearer_token="secret")

    headers = client._build_headers()

    assert headers["Authorization"] == "Bearer secret"
    assert headers["Accept"] == "application/json"


def test_parse_list_fixture_extracts_standardized_notes():
    from core.biji_client import BijiClient

    raw = _load_json(_LIST_FIXTURE)

    notes = BijiClient.parse_list_response(raw)

    assert len(notes) == 3
    assert notes[0]["note_id"] == "1905199764681666160"
    assert notes[0]["title"] == "AI时代小团队效率提升实践与Agent发展趋势分享"
    assert notes[0]["summary"].startswith("录音时间：2026-03-24 19:14:28")
    assert notes[0]["source_url"] == "https://www.biji.com/note/1905199764681666160"
    assert notes[0]["created_at"] == "2026-03-24 19:14:28"
    assert notes[0]["updated_at"] == "2026-03-24 20:34:16"


def test_parse_detail_fixture_extracts_standardized_detail():
    from core.biji_client import BijiClient

    raw = _load_json(_DETAIL_FIXTURE)

    detail = BijiClient.parse_detail_response(raw)

    assert detail["note_id"] == "1905199764681666160"
    assert detail["title"] == "AI时代小团队效率提升实践与Agent发展趋势分享"
    assert detail["summary"].startswith("录音时间：2026-03-24 19:14:28")
    assert detail["source_url"] == "https://www.biji.com/note/1905199764681666160"
    assert detail["raw_content"].startswith("### 📑 智能总结")
    assert detail["assets"] == [
        {
            "asset_url": "https://assets.example.invalid/biji/audio-sample.mp3",
            "asset_type": "audio",
            "title": "",
            "mime_type": None,
        }
    ]


def test_list_notes_retries_server_errors_and_returns_parsed_notes():
    from core.biji_client import BijiClient

    payload = _load_json(_LIST_FIXTURE)
    responses = [
        _FakeResponse(status_code=500, payload={"error": "boom"}),
        _FakeResponse(status_code=502, payload={"error": "still-boom"}),
        _FakeResponse(status_code=200, payload=payload),
    ]

    client = BijiClient(
        api_base="https://notes-api.biji.com",
        bearer_token="secret",
        timeout=5,
    )
    client._session.get = MagicMock(side_effect=responses)

    with patch("core.biji_client.time.sleep"):
        notes = client.list_notes(page=1, page_size=5)

    assert len(notes) == 3
    assert client._session.get.call_count == 3
    called_headers = client._session.get.call_args.kwargs["headers"]
    assert called_headers["Authorization"] == "Bearer secret"


def test_get_note_detail_raises_auth_error_on_403():
    from core.biji_client import BijiAuthError, BijiClient

    client = BijiClient(api_base="https://notes-api.biji.com", bearer_token="secret")
    client._session.get = MagicMock(return_value=_FakeResponse(status_code=403))

    with pytest.raises(BijiAuthError):
        client.get_note_detail("1905199764681666160")


def test_download_asset_writes_bytes_to_disk(tmp_path):
    from core.biji_client import BijiClient

    client = BijiClient(api_base="https://notes-api.biji.com", bearer_token="secret")
    client._session.get = MagicMock(return_value=_FakeResponse(content=b"abc123"))
    dest = tmp_path / "audio.mp3"

    client.download_asset("https://assets.example.invalid/biji/audio-sample.mp3", dest)

    assert dest.read_bytes() == b"abc123"
    called_headers = client._session.get.call_args.kwargs["headers"]
    assert called_headers["Authorization"] == "Bearer secret"
