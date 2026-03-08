"""雪球内容爬虫"""

import json
import logging
import os
import re
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

    def login_and_sync(self, user_id: int, headless: bool = False):
        """直接用 patchright 控制浏览器，完全掌控生命周期。"""
        self.sync_status = "logging_in"
        self.sync_progress = "正在启动浏览器..."
        _log("login_and_sync: starting browser...")
        try:
            from patchright.sync_api import sync_playwright

            with sync_playwright() as pw:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=self._user_data_dir,
                    headless=headless,
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = context.new_page()
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
                    self._sync_all(page, user_id)

                finally:
                    _log("closing browser context...")
                    context.close()

        except Exception as e:
            self.sync_status = "error"
            self.sync_progress = f"错误: {e}"
            _log(f"login_and_sync error: {e}")
            raise

    def _sync_all(self, page, user_id: int):
        """用 fetch() 精确分页同步所有帖子。"""
        self.sync_status = "syncing"
        total_saved = 0
        pending_images: list = []

        try:
            self._ensure_waf_ready(page)

            last_id = self.db.get_latest_post_id()
            incremental = last_id is not None
            _log(f"incremental={incremental}, last_id={last_id}")
            stop = False
            max_pages = 200
            waf_retries = 0

            pg = 1
            while not stop and pg <= max_pages:
                self.sync_progress = f"正在拉取第 {pg} 页..."
                _log(f"Fetching timeline page {pg}")

                data = self._browser_fetch_api(
                    page, _TIMELINE_API, {"user_id": user_id, "page": pg}
                )
                if data is None:
                    if waf_retries < 3:
                        _log(f"WAF retry {waf_retries + 1}/3")
                        self._ensure_waf_ready(page)
                        waf_retries += 1
                        continue
                    _log("WAF retries exhausted, stopping")
                    break
                waf_retries = 0

                posts = self._parse_timeline(data)
                if not posts:
                    _log(f"No posts on page {pg}, stopping")
                    break

                new_on_page = 0
                _log(f"Page {pg}: {len(posts)} posts")
                for post in posts:
                    if self.db.get_post(post["id"]):
                        continue  # 跳过已有帖子，继续处理本页剩余

                    if self._needs_full_fetch(post):
                        full = self._browser_fetch_api(
                            page, _SHOW_API, {"id": post["id"]}
                        )
                        if full:
                            post["text"] = full.get("text", post.get("text", ""))
                            post["title"] = full.get("title", post.get("title"))
                        # 长文拉取也需要间隔
                        page.wait_for_timeout(3000)

                    img_urls = self._extract_image_urls(post.get("text"))
                    self.db.save_post(post)
                    for seq, url in enumerate(img_urls):
                        pending_images.append((post["id"], url, seq))

                    total_saved += 1
                    new_on_page += 1
                    self.sync_count = total_saved
                    self.sync_progress = f"已保存 {total_saved} 条帖子"

                _log(f"Page {pg}: {new_on_page} new, {len(posts) - new_on_page} skipped")
                pg += 1
                # 5 秒间隔，降低被风控的风险
                page.wait_for_timeout(5000)

            # 批量下载图片
            if pending_images:
                self.sync_progress = f"正在下载 {len(pending_images)} 张图片..."
                _log(f"Downloading {len(pending_images)} images...")
                for i, (post_id, url, seq) in enumerate(pending_images):
                    local = self._download_image(post_id, url, seq)
                    self.db.save_image(post_id, url, local, seq)
                    if (i + 1) % 10 == 0:
                        self.sync_progress = f"图片 {i+1}/{len(pending_images)}"

        except Exception as e:
            _log(f"_sync_all error: {e}")
            self.sync_status = "error"
            self.sync_progress = f"同步出错: {e}"
            raise

        self.db.set_sync_state("last_sync_time", str(int(time.time())))
        self.db.set_sync_state("total_synced", str(total_saved))
        self.sync_status = "done"
        self.sync_progress = f"同步完成，共 {total_saved} 条"
        _log(f"Sync complete: {total_saved} posts, {len(pending_images)} images")

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

    def _browser_fetch_api(self, page, path: str, params: dict) -> Optional[dict]:
        """在浏览器内导航到 API URL 获取 JSON 数据。

        阿里云 WAF 会拦截 fetch() XHR 请求，但允许浏览器直接导航。
        策略：用 page.goto() 导航到 API URL，从页面 body 提取 JSON。
        """
        url = f"{_BASE_URL}{path}?{urlencode(params)}"
        try:
            resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
            status = resp.status if resp else 0
            # 等待页面内容加载
            page.wait_for_timeout(2000)

            body = page.evaluate("() => document.body?.innerText || ''")
            _log(f"goto {path} → HTTP {status}, len={len(body)}, prefix={body[:200]!r}")

            if not body or not body.strip():
                _log(f"empty body: {path}")
                return None

            # 检查是否是 WAF challenge 页面
            html = page.content()
            if 'aliyun_waf_aa' in html or '_waf_' in html[:500]:
                _log(f"WAF challenge page detected: {path}")
                # 等待 WAF JS 执行完毕
                page.wait_for_timeout(5000)
                body = page.evaluate("() => document.body?.innerText || ''")
                _log(f"after WAF wait: len={len(body)}, prefix={body[:200]!r}")
                if not body or 'aliyun_waf_aa' in page.content()[:500]:
                    _log(f"WAF still blocking: {path}")
                    return None

            try:
                return json.loads(body)
            except json.JSONDecodeError:
                # body 可能包含额外的 HTML，尝试提取 JSON 部分
                _log(f"JSON parse failed, trying to extract JSON from body")
                # 尝试用 pre 标签内容（浏览器显示 JSON 时通常包裹在 pre 里）
                pre_text = page.evaluate("""
                    () => {
                        const pre = document.querySelector('pre');
                        return pre ? pre.textContent : '';
                    }
                """)
                if pre_text:
                    return json.loads(pre_text)
                return None

        except Exception as e:
            _log(f"goto error: {path} — {e}")
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
