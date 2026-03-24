"""HTTP client for Biji note APIs."""

import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urljoin

import requests


class BijiAuthError(RuntimeError):
    """Raised when the Biji API rejects the bearer token."""


class BijiRequestError(RuntimeError):
    """Raised when the Biji API request fails after retries."""


class BijiClient:
    def __init__(
        self,
        api_base: str,
        bearer_token: str,
        timeout: int = 20,
        session: Optional[requests.Session] = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.bearer_token = bearer_token.strip()
        self.timeout = timeout
        self._session = session or requests.Session()

    def _build_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }

    def _request(self, path: str, *, params: Optional[dict[str, Any]] = None):
        url = urljoin(f"{self.api_base}/", path.lstrip("/"))
        return self._request_url(url, params=params)

    def _request_url(self, url: str, *, params: Optional[dict[str, Any]] = None):
        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                response = self._session.get(
                    url,
                    params=params,
                    headers=self._build_headers(),
                    timeout=self.timeout,
                )
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = BijiRequestError(f"Biji API temporary failure: {exc}")
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_error

            if response.status_code in (401, 403):
                raise BijiAuthError(f"Biji API authentication failed: {response.status_code}")

            if response.status_code == 429 or 500 <= response.status_code < 600:
                last_error = BijiRequestError(
                    f"Biji API temporary failure: {response.status_code}"
                )
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise last_error

            if response.status_code >= 400:
                raise BijiRequestError(f"Biji API request failed: {response.status_code}")

            return response

        raise last_error or BijiRequestError("Biji API request failed")

    def list_notes(self, page: int, page_size: int) -> list[dict]:
        response = self._request(
            "/voicenotes/web/notes",
            params={"page": page, "page_size": page_size},
        )
        return self.parse_list_response(response.json())

    def get_note_detail(self, note_id: str) -> dict:
        response = self._request(f"/voicenotes/web/notes/{note_id}")
        return self.parse_detail_response(response.json())

    def download_asset(self, url: str, dest_path: str | Path):
        response = self._request_url(url)

        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.content)

    @staticmethod
    def _make_source_url(note_id: str) -> str:
        return f"https://www.biji.com/note/{note_id}"

    @staticmethod
    def _extract_summary(item: dict) -> str:
        summary = (item.get("body_text") or item.get("content") or "").strip()
        return summary

    @staticmethod
    def _normalize_assets(item: dict) -> list[dict]:
        assets: list[dict] = []

        for asset in item.get("attachments") or []:
            asset_url = asset.get("url")
            if not asset_url:
                continue
            assets.append(
                {
                    "asset_url": asset_url,
                    "asset_type": asset.get("type") or "attachment",
                    "title": asset.get("title") or "",
                    "mime_type": asset.get("mime_type"),
                }
            )

        for image_url in (item.get("original_images") or []) + (item.get("small_images") or []):
            if not image_url:
                continue
            assets.append(
                {
                    "asset_url": image_url,
                    "asset_type": "image",
                    "title": "",
                    "mime_type": None,
                }
            )

        return assets

    @classmethod
    def parse_list_response(cls, raw: dict) -> list[dict]:
        items = (((raw.get("c") or {}).get("list")) or [])
        notes = []
        for item in items:
            note_id = str(item.get("note_id") or item.get("id") or "")
            if not note_id:
                continue
            notes.append(
                {
                    "note_id": note_id,
                    "title": item.get("title") or "",
                    "summary": cls._extract_summary(item),
                    "source_url": cls._make_source_url(note_id),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "status": item.get("status"),
                }
            )
        return notes

    @classmethod
    def parse_detail_response(cls, raw: dict) -> dict:
        item = raw.get("c") or {}
        note_id = str(item.get("note_id") or item.get("id") or "")
        return {
            "note_id": note_id,
            "title": item.get("title") or "",
            "summary": cls._extract_summary(item),
            "source_url": cls._make_source_url(note_id),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "raw_content": item.get("content") or item.get("body_text") or "",
            "assets": cls._normalize_assets(item),
            "status": item.get("status"),
        }
