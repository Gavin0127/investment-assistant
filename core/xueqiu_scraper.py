"""雪球内容爬虫"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

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

            def extract_cookies(page):
                import asyncio
                self.sync_progress = "请在浏览器中登录雪球..."
                for _ in range(300):  # 5 min timeout
                    cookies = asyncio.get_event_loop().run_until_complete(
                        page.context.cookies()
                    )
                    cookie_dict = {c["name"]: c["value"] for c in cookies}
                    if "u" in cookie_dict:
                        self._cookies = cookie_dict
                        return page
                    asyncio.get_event_loop().run_until_complete(
                        page.wait_for_timeout(1000)
                    )
                raise TimeoutError("登录超时（5分钟）")

            StealthyFetcher.fetch(
                f"{_BASE_URL}/",
                headless=headless,
                page_action=extract_cookies,
            )
            self._sync_all(user_id)
        except Exception as e:
            self.sync_status = "error"
            self.sync_progress = f"错误: {e}"
            logger.error("Sync failed: %s", e)
            raise

    def _sync_all(self, user_id: int):
        """Fetch all timeline pages and save posts."""
        self.sync_status = "syncing"
        last_id = self.db.get_latest_post_id()
        incremental = last_id is not None

        page = 1
        total_saved = 0
        stop = False

        while not stop:
            self.sync_progress = f"正在拉取第 {page} 页..."
            logger.info("Fetching timeline page %d", page)

            data = self._api_get(_TIMELINE_API, {"user_id": user_id, "page": page})
            if not data:
                break

            posts = self._parse_timeline(data)
            if not posts:
                break

            for post in posts:
                # I3: 遇到已存在的帖子标记停止，但 continue 处理完当前页
                if incremental and self.db.get_post(post["id"]):
                    stop = True
                    continue

                if self._needs_full_fetch(post):
                    full = self._fetch_full_article(post["id"])
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

            page += 1
            time.sleep(1.5)

        self.db.set_sync_state("last_sync_time", str(int(time.time())))
        self.db.set_sync_state("total_synced", str(total_saved))
        self.sync_status = "done"
        self.sync_progress = f"同步完成，共 {total_saved} 条"
        logger.info("Sync complete: %d posts saved", total_saved)

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

    def _parse_timeline(self, data: dict) -> List[Dict]:
        posts = []
        for s in data.get("statuses", []):
            post = {
                "id": s["id"],
                "user_id": s.get("user_id", 0),
                "type": s.get("type"),
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
