"""雪球内容爬虫"""

import json
import logging
import os
import re
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

    def login_and_sync(self, user_id: int, headless: bool = False):
        """Open browser for login, then sync all posts."""
        self.sync_status = "logging_in"
        self.sync_progress = "等待登录..."
        try:
            from scrapling.fetchers import StealthyFetcher

            def login_then_sync(page):
                # 检查是否已有登录 cookie（user_data_dir 持久化的）
                cookies = page.context.cookies()
                cookie_dict = {c["name"]: c["value"] for c in cookies}
                if "u" in cookie_dict:
                    logger.info("Reusing saved session, cookies: %s", list(cookie_dict.keys()))
                    self._cookies = cookie_dict
                    self._sync_via_interception(page, user_id)
                    return page

                # 需要扫码登录
                self.sync_progress = "请在浏览器中登录雪球..."
                for _ in range(300):  # 5 min timeout
                    cookies = page.context.cookies()
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    if "u" in cookie_dict:
                        logger.info("Login detected, cookies: %s", list(cookie_dict.keys()))
                        page.wait_for_timeout(2000)
                        self._cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                        self._sync_via_interception(page, user_id)
                        return page
                    page.wait_for_timeout(1000)
                raise TimeoutError("登录超时（5分钟）")

            StealthyFetcher.fetch(
                f"{_BASE_URL}/",
                headless=headless,
                timeout=60000,
                user_data_dir=self._user_data_dir,
                page_action=login_then_sync,
            )
        except Exception as e:
            self.sync_status = "error"
            self.sync_progress = f"错误: {e}"
            logger.error("Sync failed: %s", e)
            raise

    def _sync_via_interception(self, page, user_id: int):
        """导航到用户主页，拦截页面自身的 timeline API 响应来获取数据。"""
        self.sync_status = "syncing"
        last_id = self.db.get_latest_post_id()
        incremental = last_id is not None
        total_saved = 0
        stop = False
        captured_responses: list = []

        def on_response(response):
            """拦截页面发出的 timeline API 响应。"""
            if _TIMELINE_API in response.url:
                try:
                    data = response.json()
                    captured_responses.append(data)
                    logger.info("Captured timeline response, statuses=%d", len(data.get("statuses", [])))
                except Exception as e:
                    logger.warning("Failed to parse captured response: %s", e)

        page.on("response", on_response)

        # 导航到用户主页，触发第一次 timeline API 请求
        self.sync_progress = "正在加载用户主页..."
        logger.info("Navigating to user page: %s/u/%d", _BASE_URL, user_id)
        page.goto(f"{_BASE_URL}/u/{user_id}", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        # 处理首次加载捕获的数据
        pg = 1
        for data in captured_responses:
            self.sync_progress = f"正在处理第 {pg} 页..."
            posts = self._parse_timeline(data)
            for post in posts:
                if incremental and self.db.get_post(post["id"]):
                    stop = True
                    continue
                if self._needs_full_fetch(post):
                    # 长文需要完整内容，通过点击进入详情页获取
                    full = self._fetch_article_in_browser(page, post["id"])
                    if full:
                        post["text"] = full.get("text", post.get("text", ""))
                        post["title"] = full.get("title", post.get("title"))
                img_urls = self._extract_image_urls(post.get("text"))
                self.db.save_post(post)
                for seq, url in enumerate(img_urls):
                    local = self._download_image(post["id"], url, seq)
                    self.db.save_image(post["id"], url, local, seq)
                total_saved += 1
                self.sync_count = total_saved
                self.sync_progress = f"已保存 {total_saved} 条"
            pg += 1

        # 模拟滚动加载更多页
        max_scroll_pages = 50
        while not stop and pg <= max_scroll_pages:
            captured_responses.clear()
            self.sync_progress = f"正在滚动加载第 {pg} 页..."
            logger.info("Scrolling to load page %d", pg)

            # 滚动到底部触发加载
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)

            if not captured_responses:
                # 没有新的 API 响应，可能已经到底了
                logger.info("No more timeline responses captured, stopping")
                break

            for data in captured_responses:
                posts = self._parse_timeline(data)
                if not posts:
                    stop = True
                    break
                for post in posts:
                    if incremental and self.db.get_post(post["id"]):
                        stop = True
                        continue
                    if self._needs_full_fetch(post):
                        full = self._fetch_article_in_browser(page, post["id"])
                        if full:
                            post["text"] = full.get("text", post.get("text", ""))
                            post["title"] = full.get("title", post.get("title"))
                    img_urls = self._extract_image_urls(post.get("text"))
                    self.db.save_post(post)
                    for seq, url in enumerate(img_urls):
                        local = self._download_image(post["id"], url, seq)
                        self.db.save_image(post["id"], url, local, seq)
                    total_saved += 1
                    self.sync_count = total_saved
                    self.sync_progress = f"已保存 {total_saved} 条"
            pg += 1

        page.remove_listener("response", on_response)
        self.db.set_sync_state("last_sync_time", str(int(time.time())))
        self.db.set_sync_state("total_synced", str(total_saved))
        self.sync_status = "done"
        self.sync_progress = f"同步完成，共 {total_saved} 条"
        logger.info("Sync complete: %d posts saved", total_saved)

    def _fetch_article_in_browser(self, page, post_id: int) -> Optional[dict]:
        """在浏览器内通过拦截获取长文详情。"""
        captured = []

        def on_show_response(response):
            if _SHOW_API in response.url and str(post_id) in response.url:
                try:
                    captured.append(response.json())
                except Exception:
                    pass

        page.on("response", on_show_response)
        try:
            page.goto(f"{_BASE_URL}/{post_id}", wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(2000)
        except Exception as e:
            logger.warning("Failed to load article %d: %s", post_id, e)
        finally:
            page.remove_listener("response", on_show_response)

        if captured:
            return captured[0]
        # fallback: 从页面 DOM 提取
        try:
            text = page.evaluate("() => document.querySelector('.article__bd')?.innerHTML || ''")
            title = page.evaluate("() => document.querySelector('.article__title')?.textContent || ''")
            if text:
                return {"text": text, "title": title}
        except Exception:
            pass
        return None

    def _api_get(self, path: str, params: dict) -> Optional[dict]:
        try:
            resp = requests.get(
                f"{_BASE_URL}{path}", params=params,
                cookies=self._cookies, headers=self._headers, timeout=15,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error("API request failed: %s %s — %s", path, params, e)
            return None

    def _ensure_waf_ready(self, page):
        """导航到雪球首页，等待 WAF JS Challenge 完成。"""
        self.sync_progress = "正在通过 WAF 验证..."
        page.goto(f"{_BASE_URL}/", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

    def _browser_fetch_api(self, page, path: str, params: dict) -> Optional[dict]:
        """在浏览器内用 fetch() 调 API，自动携带 WAF cookie。"""
        url = f"{_BASE_URL}{path}?{urlencode(params)}"
        try:
            resp_text = page.evaluate("""
                async (url) => {
                    const resp = await fetch(url, {
                        credentials: 'include',
                        headers: { 'Accept': 'application/json' }
                    });
                    return await resp.text();
                }
            """, url)
            if not resp_text or resp_text.lstrip().startswith('<'):
                logger.warning("WAF blocked fetch for %s", path)
                return None
            return json.loads(resp_text)
        except Exception as e:
            logger.error("Browser fetch failed: %s %s — %s", path, params, e)
            return None

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
            posts.append(post)
        return posts

    def _needs_full_fetch(self, post: dict) -> bool:
        return (
            str(post.get("type")) == "3"
            and post.get("is_column")
            and not post.get("text")
        )

    def _fetch_full_article(self, post_id: int) -> Optional[dict]:
        data = self._api_get(_SHOW_API, {"id": post_id})
        return data if data else None

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
