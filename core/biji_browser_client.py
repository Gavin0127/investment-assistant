"""Browser-session backed Biji client."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlencode, urljoin

import requests

from core.biji_client import BijiAuthError, BijiClient, BijiRequestError


class _BrowserResponse:
    def __init__(self, status_code: int, payload: Optional[dict] = None, content: bytes = b""):
        self.status_code = status_code
        self._payload = payload or {}
        self.content = content

    def json(self) -> dict:
        return self._payload


class BijiBrowserClient(BijiClient):
    """Use a persistent browser profile as the primary auth mechanism."""

    def __init__(
        self,
        api_base: str,
        profile_dir: str,
        *,
        note_url: str = "https://www.biji.com/note",
        timeout: int = 20,
        headless: bool = True,
        session: Optional[requests.Session] = None,
        playwright_factory=None,
    ):
        super().__init__(api_base=api_base, bearer_token="", timeout=timeout, session=session)
        self.profile_dir = str(Path(profile_dir).expanduser())
        self.note_url = note_url
        self.headless = headless
        self._playwright_factory = playwright_factory
        self._playwright_manager = None
        self._playwright = None
        self._context = None
        self._page = None
        self._page_ready = False

    def _build_headers(self) -> dict[str, str]:
        headers = super()._build_headers()
        headers.pop("Authorization", None)
        return headers

    def close(self) -> None:
        if self._context is not None:
            self._context.close()
            self._context = None
        if self._playwright_manager is not None:
            self._playwright_manager.__exit__(None, None, None)
            self._playwright_manager = None
            self._playwright = None
        self._page = None
        self._page_ready = False

    def login(self, timeout_seconds: int = 300, poll_interval_ms: int = 1000) -> None:
        page = self._ensure_browser()
        page.goto(self.note_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
        self._page_ready = True

        deadline = time.time() + timeout_seconds
        while True:
            response = self._browser_fetch_response(
                urljoin(f"{self.api_base}/", "voicenotes/web/user/info")
            )
            if response.status_code < 400:
                return
            if response.status_code not in (401, 403):
                raise BijiRequestError(
                    f"Biji browser login check failed: {response.status_code}"
                )
            if time.time() >= deadline:
                raise BijiAuthError("Biji browser login timed out")
            page.wait_for_timeout(poll_interval_ms)

    def _request(self, path: str, *, params: Optional[dict[str, Any]] = None):
        url = urljoin(f"{self.api_base}/", path.lstrip("/"))
        return self._browser_request_with_retries(url, params=params)

    def _request_url(self, url: str, *, params: Optional[dict[str, Any]] = None):
        if url.startswith(f"{self.api_base}/voicenotes/web/"):
            return self._browser_request_with_retries(url, params=params)

        last_error: Optional[Exception] = None
        for attempt in range(3):
            self._sync_session_cookies()
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

    def _browser_request_with_retries(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
    ) -> _BrowserResponse:
        last_error: Optional[Exception] = None

        for attempt in range(3):
            try:
                response = self._browser_fetch_response(url, params=params)
            except BijiRequestError as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise

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

    def _browser_fetch_response(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
    ) -> _BrowserResponse:
        page = self._ensure_page_ready()
        full_url = url
        if params:
            full_url = f"{url}?{urlencode(params, doseq=True)}"

        try:
            result = page.evaluate(
                """
                async ({ url, headers }) => {
                  const response = await fetch(url, {
                    method: "GET",
                    credentials: "include",
                    headers,
                  });
                  const text = await response.text();
                  return { status: response.status, text };
                }
                """,
                {
                    "url": full_url,
                    "headers": {
                        "Accept": "application/json",
                    },
                },
            )
        except Exception as exc:  # pragma: no cover - patchright exceptions vary by platform
            raise BijiRequestError(f"Biji browser request failed: {exc}") from exc

        raw_text = result.get("text") or ""
        payload = {}
        if raw_text:
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError as exc:
                raise BijiRequestError("Biji browser returned non-JSON response") from exc
        return _BrowserResponse(
            status_code=int(result.get("status") or 0),
            payload=payload,
            content=raw_text.encode("utf-8"),
        )

    def _ensure_page_ready(self):
        page = self._ensure_browser()
        if not self._page_ready:
            page.goto(self.note_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            self._page_ready = True
        return page

    def _ensure_browser(self):
        if self._page is not None:
            return self._page

        playwright_factory = self._playwright_factory
        if playwright_factory is None:
            from patchright.sync_api import sync_playwright

            playwright_factory = sync_playwright

        Path(self.profile_dir).mkdir(parents=True, exist_ok=True)
        self._playwright_manager = playwright_factory()
        self._playwright = self._playwright_manager.__enter__()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=self.profile_dir,
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        self._page = self._context.new_page()
        self._page.set_default_timeout(self.timeout * 1000)
        return self._page

    def _sync_session_cookies(self) -> None:
        if self._context is None:
            return
        for cookie in self._context.cookies():
            self._session.cookies.set(
                cookie["name"],
                cookie["value"],
                domain=cookie.get("domain"),
                path=cookie.get("path", "/"),
            )
