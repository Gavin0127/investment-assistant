"""雪球内容爬虫"""

import json
import logging
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlencode, urlparse

import requests

from core.xueqiu_db import XueqiuDB

logger = logging.getLogger(__name__)

_BASE_URL = "https://xueqiu.com"
_TIMELINE_API = "/v4/statuses/user_timeline.json"
_SHOW_API = "/statuses/show.json"
_PAGE_SIZE = 20  # 雪球 API 每页返回条数


def _log(msg: str):
    """写入 stderr 并 flush，确保 Flask debug 模式下日志可见。"""
    print(f"[xueqiu] {msg}", file=sys.stderr, flush=True)


class XueqiuScraper:
    def __init__(self, db_path: str, image_dir: str):
        self.db = XueqiuDB(db_path)
        self.image_dir = image_dir
        os.makedirs(image_dir, exist_ok=True)
        # 浏览器 user data 目录，持久化 cookie 和登录状态
        self._user_data_dir = os.path.join(os.path.dirname(db_path), "xueqiu_browser")
        os.makedirs(self._user_data_dir, exist_ok=True)
        self._cookies: dict = {}
        self._headers: dict = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/131.0.0.0 Safari/537.36",
            "Origin": _BASE_URL,
            "Referer": f"{_BASE_URL}/",
        }
        # Sync progress tracking
        self.sync_status: str = "idle"  # idle | logging_in | syncing | done | error
        self.sync_progress: str = ""
        self.sync_count: int = 0

    def _resolve_browser_executable(self) -> Optional[str]:
        """优先复用系统已安装的 Chrome，避免 patchright 内置 Chromium 启动崩溃。"""
        env_path = os.environ.get("XUEQIU_CHROME_PATH")
        if env_path:
            candidate = Path(env_path).expanduser()
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

        home = Path(os.path.expanduser("~"))
        agent_browser_root = home / ".agent-browser" / "browsers"
        bundled = sorted(
            agent_browser_root.glob(
                "chrome-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            ),
            reverse=True,
        )
        for candidate in bundled:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

        common_candidates = [
            Path("/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"),
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for candidate in common_candidates:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)

        return None

    def _build_launch_kwargs(self, headless: bool) -> dict:
        """构造浏览器启动参数。"""
        kwargs = {
            "user_data_dir": self._user_data_dir,
            "headless": headless,
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        executable_path = self._resolve_browser_executable()
        if executable_path:
            kwargs["executable_path"] = executable_path
        return kwargs

    def _cleanup_stale_profile_locks(self) -> None:
        """清理崩溃后残留的 Chromium profile 锁，避免下次启动直接失败。"""
        profile_dir = Path(self._user_data_dir)
        lock_paths = [
            profile_dir / "SingletonLock",
            profile_dir / "SingletonSocket",
            profile_dir / "SingletonCookie",
        ]
        if not any(path.exists() or path.is_symlink() for path in lock_paths):
            return

        try:
            result = subprocess.run(
                ["pgrep", "-af", self._user_data_dir],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return

        if result.stdout.strip():
            _log("browser profile appears in use, skip stale lock cleanup")
            return

        for path in lock_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to remove stale lock: %s", path)

    def _resolve_cdp_ws_url(self) -> Optional[str]:
        """解析可复用的本机 Chrome CDP 地址。"""
        ws_url = os.environ.get("XUEQIU_CDP_WS_URL")
        if ws_url:
            return ws_url

        http_base = os.environ.get("XUEQIU_CDP_HTTP_URL", "http://127.0.0.1:9222")
        try:
            resp = requests.get(f"{http_base.rstrip('/')}/json/version", timeout=2)
            resp.raise_for_status()
            return resp.json().get("webSocketDebuggerUrl")
        except Exception:
            return None

    def _open_browser_session(self, pw, headless: bool) -> dict:
        """打开浏览器会话。优先复用主 Chrome 的 CDP 会话，失败时回退到本地持久化 profile。"""
        cdp_ws_url = self._resolve_cdp_ws_url()
        if cdp_ws_url:
            _log(f"using cdp browser: {cdp_ws_url}")
            browser = pw.chromium.connect_over_cdp(cdp_ws_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            return {
                "mode": "cdp",
                "browser": browser,
                "context": context,
                "page": page,
            }

        self._cleanup_stale_profile_locks()
        launch_kwargs = self._build_launch_kwargs(headless=headless)
        if launch_kwargs.get("executable_path"):
            _log(f"using browser executable: {launch_kwargs['executable_path']}")
        else:
            _log("using patchright bundled chromium")

        context = pw.chromium.launch_persistent_context(**launch_kwargs)
        page = context.new_page()
        return {
            "mode": "launch",
            "browser": None,
            "context": context,
            "page": page,
        }

    def _close_browser_session(self, session: dict) -> None:
        """关闭当前爬取会话，但不破坏用户的主 Chrome。"""
        if session["mode"] == "cdp":
            try:
                if not session["page"].is_closed():
                    session["page"].close()
            except Exception:
                pass
            try:
                session["browser"].close()
            except Exception:
                pass
            return

        session["context"].close()

    @staticmethod
    def _raise_waf_blocked() -> None:
        raise RuntimeError("雪球访问验证未通过，请在浏览器完成滑块验证后重试")

    def login_and_sync(self, user_id: int, headless: bool = False, max_pages: int = 5):
        """直接用 patchright 控制浏览器，完全掌控生命周期。"""
        self.sync_status = "logging_in"
        self.sync_progress = "正在启动浏览器..."
        _log("login_and_sync: starting browser...")
        try:
            from patchright.sync_api import sync_playwright

            with sync_playwright() as pw:
                session = self._open_browser_session(pw, headless=headless)
                context = session["context"]
                page = session["page"]
                page.set_default_timeout(120_000)  # 2 分钟超时，给足操作时间

                try:
                    # 导航到雪球首页
                    self.sync_progress = "正在打开雪球..."
                    _log("navigating to xueqiu.com...")
                    page.goto(f"{_BASE_URL}/", wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(3000)

                    # 检查是否已有登录 cookie
                    cookies = context.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    if "u" in cookie_dict:
                        _log(f"Reusing saved session, cookie keys: {list(cookie_dict.keys())}")
                        self._cookies = cookie_dict
                    else:
                        # 等待扫码登录
                        self.sync_progress = "请在浏览器中扫码登录雪球..."
                        _log("waiting for login (5 min timeout)...")
                        for i in range(300):
                            cookies = context.cookies()
                            cookie_dict = {c["name"]: c["value"] for c in cookies}
                            if "u" in cookie_dict:
                                _log(f"Login detected at iteration {i}")
                                page.wait_for_timeout(2000)
                                self._cookies = {c["name"]: c["value"] for c in context.cookies()}
                                break
                            page.wait_for_timeout(1000)
                        else:
                            raise TimeoutError("登录超时（5分钟）")

                    # 登录成功，开始同步
                    self._sync_all(page, user_id, max_pages=max_pages)

                finally:
                    _log("closing browser context...")
                    self._close_browser_session(session)

        except Exception as e:
            self.sync_status = "error"
            self.sync_progress = f"错误: {e}"
            _log(f"login_and_sync error: {e}")
            raise

    def _sync_all(self, page, user_id: int, max_pages: int = 5):
        """双阶段增量同步。

        阶段 1（追新）：从 page 1 开始，upsert 所有帖子，直到连上已同步区或用完配额。
        阶段 2（深挖）：从历史断点继续往后翻，用剩余配额。
        """
        self.sync_status = "syncing"
        new_count = 0
        updated_count = 0
        pending_images: list = []
        pages_used = 0
        all_timestamps: list[int] = []

        try:
            self._ensure_waf_ready(page)
            cursor = self.db.get_sync_cursor(user_id)

            # ── 阶段 1：追新 ──
            connected = False
            if cursor is not None:
                pg = 1
                while pages_used < max_pages:
                    self.sync_progress = f"正在检查新帖... (第 {pg} 页)"
                    _log(f"Phase 1: fetching page {pg}")

                    data = self._fetch_with_waf_retry(page, user_id, pg)
                    if data is None:
                        if pg == 1 and not all_timestamps:
                            self._raise_waf_blocked()
                        break
                    pages_used += 1

                    posts = self._parse_timeline(data)
                    if not posts:
                        _log(f"Phase 1: page {pg} empty, stopping")
                        break

                    page_oldest_at = min(p["created_at"] for p in posts)
                    page_new, page_updated = self._process_posts(
                        page, posts, pending_images, all_timestamps
                    )
                    new_count += page_new
                    updated_count += page_updated
                    self.sync_count = new_count
                    self.sync_progress = f"发现 {new_count} 条新帖，更新 {updated_count} 条"

                    if not cursor.get("has_gap", False):
                        if page_oldest_at <= cursor["newest_synced_at"]:
                            connected = True
                            _log(f"Phase 1: connected at page {pg} (no gap)")
                            break
                    else:
                        if page_oldest_at <= cursor["oldest_synced_at"]:
                            connected = True
                            _log(f"Phase 1: connected at page {pg} (gap filled)")
                            break

                    pg += 1
                    page.wait_for_timeout(5000)

                if all_timestamps:
                    cursor["newest_synced_at"] = max(
                        cursor["newest_synced_at"], max(all_timestamps)
                    )

                if not connected:
                    cursor["has_gap"] = True
                    if all_timestamps:
                        cursor["oldest_synced_at"] = min(
                            cursor["oldest_synced_at"], min(all_timestamps)
                        )
                    cursor["total_posts"] = self.db.count_posts(user_id)
                    self.db.set_sync_cursor(user_id, cursor)
                    self._finish_sync(
                        user_id, new_count, updated_count, pending_images, page
                    )
                    return
                else:
                    cursor["has_gap"] = False

            # ── 阶段 2：深挖历史 ──
            remaining = max_pages - pages_used
            if remaining <= 0:
                if cursor:
                    cursor["total_posts"] = self.db.count_posts(user_id)
                    self.db.set_sync_cursor(user_id, cursor)
                self._finish_sync(
                    user_id, new_count, updated_count, pending_images, page
                )
                return

            if cursor is None:
                existing_count = self.db.count_posts(user_id)
                start_page = (existing_count // _PAGE_SIZE + 1) if existing_count > 0 else 1
                _log(f"Phase 2: first V2 sync, existing={existing_count}, start_page={start_page}")
            else:
                page_offset = new_count // _PAGE_SIZE
                start_page = cursor.get("next_history_page", 1) + page_offset

            pg = start_page
            self.sync_progress = "正在定位历史断点..."
            _log(f"Phase 2: starting at page {pg} (remaining={remaining})")

            locate_attempts = 0
            located_posts = None  # I1: 定位到的页直接传给深挖循环，避免重复请求
            while locate_attempts < 3 and remaining > 0:
                data = self._fetch_with_waf_retry(page, user_id, pg)
                if data is None:
                    if pg == start_page and not all_timestamps:
                        self._raise_waf_blocked()
                    break

                posts = self._parse_timeline(data)
                if not posts:
                    _log(f"Phase 2: page {pg} empty, history done")
                    if cursor is None:
                        cursor = self._make_cursor(all_timestamps)
                    cursor["history_done"] = True
                    break

                # I4: 批量检查是否有新帖，避免逐条查询
                existing_ids = {
                    p["id"] for p in posts if self.db.get_post(p["id"])
                }
                new_on_page = len(posts) - len(existing_ids)
                if new_on_page > 0:
                    _log(f"Phase 2: found {new_on_page} new posts at page {pg}")
                    located_posts = posts
                    break

                _log(f"Phase 2: page {pg} all synced, locating...")
                page_new, page_updated = self._process_posts(
                    page, posts, pending_images, all_timestamps
                )
                updated_count += page_updated
                pg += 1
                locate_attempts += 1
                remaining -= 1
                page.wait_for_timeout(5000)

            while remaining > 0:
                # I1: 首次迭代复用定位循环已拿到的数据
                if located_posts is not None:
                    posts = located_posts
                    located_posts = None
                else:
                    data = self._fetch_with_waf_retry(page, user_id, pg)
                    if data is None:
                        break

                    posts = self._parse_timeline(data)
                    if not posts:
                        _log(f"Phase 2: page {pg} empty, history done")
                        if cursor is None:
                            cursor = self._make_cursor(all_timestamps)
                        cursor["history_done"] = True
                        break

                remaining -= 1
                self.sync_progress = (
                    f"正在同步历史帖子 (第 {pg} 页)，"
                    f"已保存 {new_count} 条新帖"
                )

                page_new, page_updated = self._process_posts(
                    page, posts, pending_images, all_timestamps
                )
                new_count += page_new
                updated_count += page_updated
                self.sync_count = new_count

                pg += 1
                page.wait_for_timeout(5000)

            if cursor is None:
                cursor = self._make_cursor(all_timestamps)
            if all_timestamps:
                cursor["newest_synced_at"] = max(
                    cursor.get("newest_synced_at", 0), max(all_timestamps)
                )
                cursor["oldest_synced_at"] = min(
                    cursor.get("oldest_synced_at", 9999999999999),
                    min(all_timestamps),
                )
            cursor["next_history_page"] = pg
            cursor["total_posts"] = self.db.count_posts(user_id)
            self.db.set_sync_cursor(user_id, cursor)

        except Exception as e:
            _log(f"_sync_all error: {e}")
            self.sync_status = "error"
            self.sync_progress = f"同步出错: {e}"
            raise

        self._finish_sync(user_id, new_count, updated_count, pending_images, page)

    def _fetch_with_waf_retry(self, page, user_id: int, pg: int) -> Optional[dict]:
        """Fetch a timeline page with WAF retry logic."""
        for attempt in range(4):
            data = self._browser_fetch_api(
                page, _TIMELINE_API, {"user_id": user_id, "page": pg}
            )
            if data is not None:
                return data
            if attempt < 3:
                _log(f"WAF retry {attempt + 1}/3 for page {pg}")
                self._ensure_waf_ready(page)
        _log(f"WAF retries exhausted for page {pg}")
        return None

    def _process_posts(
        self, page, posts: list, pending_images: list, all_timestamps: list
    ) -> tuple[int, int]:
        """Process a page of posts: upsert, collect images. Returns (new, updated)."""
        new_count = 0
        updated_count = 0
        for post in posts:
            existing = self.db.get_post(post["id"])
            all_timestamps.append(post["created_at"])

            if not existing and self._needs_full_fetch(post):
                full = self._browser_fetch_api(
                    page, _SHOW_API, {"id": post["id"]}
                )
                if full:
                    post["text"] = full.get("text", post.get("text", ""))
                    post["title"] = full.get("title", post.get("title"))
                page.wait_for_timeout(3000)

            self.db.save_post(post)

            if existing:
                updated_count += 1
            else:
                new_count += 1
                img_urls = self._extract_image_urls(post.get("text"))
                for seq, url in enumerate(img_urls):
                    pending_images.append((post["id"], url, seq))

        return new_count, updated_count

    @staticmethod
    def _make_cursor(timestamps: list) -> dict:
        """Create a new cursor from collected timestamps."""
        return {
            "newest_synced_at": max(timestamps) if timestamps else 0,
            "oldest_synced_at": min(timestamps) if timestamps else 0,
            "next_history_page": 1,
            "total_posts": 0,
            "history_done": False,
            "has_gap": False,
        }

    def _finish_sync(
        self, user_id: int, new_count: int, updated_count: int,
        pending_images: list, page,
    ):
        """Download images and finalize sync status."""
        if pending_images:
            self.sync_progress = f"正在下载 {len(pending_images)} 张图片..."
            _log(f"Downloading {len(pending_images)} images...")
            for i, (post_id, url, seq) in enumerate(pending_images):
                local = self._download_image(post_id, url, seq)
                self.db.save_image(post_id, url, local, seq)
                if (i + 1) % 10 == 0:
                    self.sync_progress = f"图片 {i+1}/{len(pending_images)}"

        self.db.set_sync_state("last_sync_time", str(int(time.time())))
        self.db.set_sync_state("total_synced", str(new_count + updated_count))
        self.sync_status = "done"
        self.sync_progress = f"同步完成：{new_count} 条新帖，{updated_count} 条更新"
        _log(f"Sync complete: {new_count} new, {updated_count} updated, {len(pending_images)} images")

    def _ensure_waf_ready(self, page):
        """导航到雪球首页，等待页面正常加载（非 WAF challenge 页面）。

        雪球 WAF 机制：首次访问 API 返回含 JS challenge 的 HTML（<textarea id="renderData">），
        浏览器执行 JS 后设置 WAF cookie，后续请求才返回正常 JSON。
        策略：导航到首页，等待页面标题变为正常内容（非空白/challenge 页面）。
        """
        self.sync_progress = "正在通过 WAF 验证..."
        _log("_ensure_waf_ready: navigating to homepage...")
        page.goto(f"{_BASE_URL}/", wait_until="domcontentloaded", timeout=30000)

        # 等待页面真正加载完成（最多 30 秒）
        for i in range(30):
            page.wait_for_timeout(1000)
            try:
                # 检查页面是否已经渲染出正常内容（雪球首页有 .home__stock-index 等元素）
                has_content = page.evaluate("""
                    () => {
                        // 检查是否有正常页面内容（非 WAF challenge）
                        return !!(
                            document.querySelector('nav') ||
                            document.querySelector('.nav') ||
                            document.querySelector('[class*="stock"]') ||
                            document.querySelector('[class*="home"]') ||
                            document.title.includes('雪球')
                        );
                    }
                """)
                if has_content:
                    _log(f"_ensure_waf_ready: page loaded after {i+1}s")
                    # 额外等 2 秒让所有 JS 执行完
                    page.wait_for_timeout(2000)
                    return
            except Exception:
                pass
            if i % 5 == 4:
                title = page.title()
                _log(f"_ensure_waf_ready: waiting... ({i+1}s, title={title!r})")

        _log("_ensure_waf_ready: page not fully loaded after 30s, trying anyway")
        cookies = page.context.cookies()
        _log(f"_ensure_waf_ready: cookies={[c['name'] for c in cookies]}")

    def _parse_api_payload(self, path: str, text: str) -> Optional[dict]:
        """解析 API 返回文本。HTML/WAF 返回 None，登录态错误抛异常。"""
        if not text or not text.strip():
            _log(f"empty body: {path}")
            return None

        stripped = text.lstrip()
        if stripped.startswith("<"):
            _log(f"non-json html response: {path}")
            return None

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            _log(f"JSON parse failed: {path}")
            return None

        error_code = str(data.get("error_code") or "")
        if error_code in {"10022", "400016"}:
            raise RuntimeError("雪球登录态不足，请在浏览器中重新登录后重试")

        return data

    def _browser_goto_api(self, page, path: str, params: dict) -> Optional[dict]:
        """回退方案：直接导航到 API URL，从页面 body 中提取 JSON。"""
        url = f"{_BASE_URL}{path}?{urlencode(params)}"
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 0
            page.wait_for_timeout(2000)

            body = page.evaluate("() => document.body?.innerText || ''")
            _log(f"goto {path} → HTTP {status}, len={len(body)}, prefix={body[:200]!r}")

            html = page.content()
            if 'aliyun_waf_aa' in html or '_waf_' in html[:500]:
                _log(f"WAF challenge page detected: {path}")
                page.wait_for_timeout(5000)
                body = page.evaluate("() => document.body?.innerText || ''")
                _log(f"after WAF wait: len={len(body)}, prefix={body[:200]!r}")
                if not body or 'aliyun_waf_aa' in page.content()[:500]:
                    _log(f"WAF still blocking: {path}")
                    return None

            data = self._parse_api_payload(path, body)
            if data is not None:
                return data

            _log("JSON parse failed, trying to extract JSON from body")
            pre_text = page.evaluate("""
                () => {
                    const pre = document.querySelector('pre');
                    return pre ? pre.textContent : '';
                }
            """)
            return self._parse_api_payload(path, pre_text)
        except Exception as e:
            _log(f"goto error: {path} — {e}")
            return None

    def _browser_fetch_api(self, page, path: str, params: dict) -> Optional[dict]:
        """优先在浏览器内用 fetch() 调 API，必要时回退到 goto。"""
        url = f"{_BASE_URL}{path}?{urlencode(params)}"
        try:
            fetch_result = page.evaluate("""
                async (url) => {
                    const controller = new AbortController();
                    const timeoutId = setTimeout(() => controller.abort('timeout'), 15000);
                    try {
                        const resp = await fetch(url, {
                            credentials: 'include',
                            headers: { 'Accept': 'application/json' },
                            signal: controller.signal,
                        });
                        const text = await resp.text();
                        return { ok: true, status: resp.status, text };
                    } catch (e) {
                        return { ok: false, error: String(e) };
                    } finally {
                        clearTimeout(timeoutId);
                    }
                }
            """, url)
            if fetch_result.get("ok"):
                text = fetch_result.get("text") or ""
                status = fetch_result.get("status")
                _log(f"fetch {path} → HTTP {status}, len={len(text)}, prefix={text[:200]!r}")
                data = self._parse_api_payload(path, text)
                if data is not None:
                    return data
                _log(f"fetch returned non-json, fallback to goto: {path}")
            else:
                _log(f"fetch error: {path} — {fetch_result.get('error')}")
        except RuntimeError:
            raise
        except Exception as e:
            _log(f"fetch exception: {path} — {e}")

        return self._browser_goto_api(page, path, params)

    def _parse_timeline(self, data: dict) -> List[Dict]:
        posts = []
        for s in data.get("statuses", []):
            post = {
                "id": s["id"],
                "user_id": s.get("user_id", 0),
                "type": str(s.get("type") or "2"),
                "is_column": bool(s.get("is_column")),
                "title": s.get("title") or None,
                "text": s.get("text") or "",
                "description": s.get("description") or "",
                "created_at": s.get("created_at", 0),
                "edited_at": s.get("edited_at") or None,
                "target": s.get("target") or "",
                "retweet_status_id": s.get("retweet_status_id", 0),
                "retweet_text": None,
                "reply_count": s.get("reply_count", 0),
                "like_count": s.get("like_count", 0),
                "retweet_count": s.get("retweet_count", 0),
                "view_count": s.get("view_count", 0),
            }
            rt = s.get("retweeted_status")
            if rt and isinstance(rt, dict):
                post["retweet_text"] = rt.get("text") or None
                post["retweet_description"] = rt.get("description") or None
            posts.append(post)
        return posts

    def _needs_full_fetch(self, post: dict) -> bool:
        return (
            str(post.get("type")) == "3"
            and post.get("is_column")
            and not post.get("text")
        )

    def _extract_image_urls(self, html: Optional[str]) -> List[str]:
        if not html:
            return []
        return re.findall(r'<img[^>]+src="([^"]+)"', html)

    def _download_image(self, post_id: int, url: str, seq: int) -> Optional[str]:
        # M3: 流式下载 + 10MB 大小限制
        _MAX_IMAGE_SIZE = 10 * 1024 * 1024
        try:
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix or ".jpg"
            filename = f"{seq:03d}{ext}"
            local_dir = Path(self.image_dir) / str(post_id)
            local_dir.mkdir(parents=True, exist_ok=True)
            local_path = local_dir / filename
            resp = requests.get(url, headers=self._headers, timeout=15, stream=True)
            resp.raise_for_status()
            size = 0
            chunks = []
            for chunk in resp.iter_content(chunk_size=8192):
                size += len(chunk)
                if size > _MAX_IMAGE_SIZE:
                    logger.warning("Image too large (>10MB), skipping: %s", url)
                    return None
                chunks.append(chunk)
            local_path.write_bytes(b"".join(chunks))
            return str(local_path)
        except Exception as e:
            logger.warning("Image download failed: %s — %s", url, e)
            return None
