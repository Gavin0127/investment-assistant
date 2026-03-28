"""Tests for XueqiuScraper (mocked network)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def scraper(tmp_path):
    from core.xueqiu_scraper import XueqiuScraper
    db_path = str(tmp_path / "test.db")
    image_dir = str(tmp_path / "images")
    return XueqiuScraper(db_path, image_dir)


class TestParseTimelineResponse:
    def test_parse_posts(self, scraper):
        raw = {
            "statuses": [
                {
                    "id": 111,
                    "user_id": 1936609590,
                    "type": "2",
                    "is_column": False,
                    "title": "",
                    "text": "<p>Hello</p>",
                    "description": "Hello",
                    "created_at": 1709856000000,
                    "edited_at": 0,
                    "target": "/1936609590/111",
                    "retweet_status_id": 0,
                    "reply_count": 1,
                    "like_count": 2,
                    "retweet_count": 0,
                    "view_count": 50,
                },
            ]
        }
        posts = scraper._parse_timeline(raw)
        assert len(posts) == 1
        assert posts[0]["id"] == 111
        assert posts[0]["text"] == "<p>Hello</p>"

    def test_parse_empty(self, scraper):
        posts = scraper._parse_timeline({"statuses": []})
        assert posts == []

    def test_parse_retweet(self, scraper):
        raw = {
            "statuses": [
                {
                    "id": 222,
                    "user_id": 1936609590,
                    "type": None,
                    "text": "转发评论",
                    "description": "转发评论",
                    "created_at": 1709856000000,
                    "retweet_status_id": 999,
                    "retweeted_status": {
                        "text": "<p>原文内容</p>",
                    },
                },
            ]
        }
        posts = scraper._parse_timeline(raw)
        assert posts[0]["retweet_status_id"] == 999
        assert posts[0]["retweet_text"] == "<p>原文内容</p>"


class TestExtractImageUrls:
    def test_extract_from_html(self, scraper):
        html = '<p>文字<img src="https://xqimg.imedao.com/a.jpg">中间<img src="https://xqimg.imedao.com/b.png"></p>'
        urls = scraper._extract_image_urls(html)
        assert len(urls) == 2
        assert urls[0] == "https://xqimg.imedao.com/a.jpg"

    def test_no_images(self, scraper):
        assert scraper._extract_image_urls("<p>纯文字</p>") == []

    def test_none_input(self, scraper):
        assert scraper._extract_image_urls(None) == []


class TestNeedsFullFetch:
    def test_column_needs_fetch(self, scraper):
        post = {"type": "3", "is_column": True, "text": ""}
        assert scraper._needs_full_fetch(post) is True

    def test_short_post_no_fetch(self, scraper):
        post = {"type": "2", "is_column": False, "text": "有内容"}
        assert scraper._needs_full_fetch(post) is False

    def test_column_with_text_no_fetch(self, scraper):
        post = {"type": "3", "is_column": True, "text": "已有全文"}
        assert scraper._needs_full_fetch(post) is False


class TestBrowserFetchApi:
    """_browser_fetch_api 优先用浏览器内 fetch()，必要时回退到 goto。"""

    def test_prefers_fetch_json_response(self, scraper):
        """fetch 返回 JSON 时直接解析，不走 goto。"""
        import json as _json
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "ok": True,
            "status": 200,
            "text": _json.dumps({"statuses": [{"id": 1}]}),
        }
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result == {"statuses": [{"id": 1}]}
        mock_page.goto.assert_not_called()

    def test_falls_back_to_goto_when_fetch_returns_html(self, scraper):
        """fetch 返回 HTML 时回退到 goto。"""
        import json as _json
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.evaluate.side_effect = [
            {"ok": True, "status": 200, "text": "<html>blocked</html>"},
            _json.dumps({"statuses": [{"id": 2}]}),
        ]
        mock_page.content.return_value = ""
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result == {"statuses": [{"id": 2}]}
        mock_page.goto.assert_called_once()

    def test_detect_waf_html_after_goto(self, scraper):
        """fetch 失败且 goto 返回 WAF challenge 页面时识别为拦截。"""
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.evaluate.side_effect = [
            {"ok": False, "error": "timeout"},
            "",
            "",
        ]
        mock_page.content.side_effect = [
            '<meta name="aliyun_waf_aa">',
            '<meta name="aliyun_waf_aa">',
        ]
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result is None

    def test_raise_clear_error_on_login_required(self, scraper):
        """API 明确返回登录错误时抛出清晰异常。"""
        mock_page = MagicMock()
        mock_page.evaluate.return_value = {
            "ok": True,
            "status": 200,
            "text": '{"error_description":"请登录雪球查看更多内容","error_code":"10022"}',
        }

        with pytest.raises(RuntimeError, match="登录态"):
            scraper._browser_fetch_api(mock_page, "/test", {"page": 2})


class TestMaxPages:
    def test_default_max_pages(self, scraper):
        """login_and_sync 默认 max_pages=5"""
        import inspect
        sig = inspect.signature(scraper.login_and_sync)
        assert sig.parameters["max_pages"].default == 5

    def test_sync_all_accepts_max_pages(self, scraper):
        """_sync_all 接受 max_pages 参数"""
        import inspect
        sig = inspect.signature(scraper._sync_all)
        assert "max_pages" in sig.parameters
        assert sig.parameters["max_pages"].default == 5


class TestBrowserLaunchConfig:
    def test_build_launch_kwargs_uses_env_override(self, scraper, monkeypatch, tmp_path):
        fake_chrome = tmp_path / "chrome-bin"
        fake_chrome.write_text("")
        fake_chrome.chmod(0o755)
        monkeypatch.setenv("XUEQIU_CHROME_PATH", str(fake_chrome))

        kwargs = scraper._build_launch_kwargs(headless=False)

        assert kwargs["headless"] is False
        assert kwargs["executable_path"] == str(fake_chrome)
        assert "--disable-blink-features=AutomationControlled" in kwargs["args"]

    def test_build_launch_kwargs_falls_back_to_agent_browser_bundle(
        self, scraper, monkeypatch, tmp_path
    ):
        fake_chrome = (
            tmp_path
            / ".agent-browser"
            / "browsers"
            / "chrome-147.0.7727.24"
            / "Google Chrome for Testing.app"
            / "Contents"
            / "MacOS"
            / "Google Chrome for Testing"
        )
        fake_chrome.parent.mkdir(parents=True)
        fake_chrome.write_text("")
        fake_chrome.chmod(0o755)
        monkeypatch.delenv("XUEQIU_CHROME_PATH", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        kwargs = scraper._build_launch_kwargs(headless=True)

        assert kwargs["headless"] is True
        assert kwargs["executable_path"] == str(fake_chrome)

    def test_build_launch_kwargs_without_env_override_keeps_default_shape(
        self, scraper, monkeypatch, tmp_path
    ):
        monkeypatch.delenv("XUEQIU_CHROME_PATH", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        kwargs = scraper._build_launch_kwargs(headless=True)

        assert kwargs["headless"] is True
        assert "--disable-blink-features=AutomationControlled" in kwargs["args"]


class TestCdpConfig:
    def test_resolve_cdp_ws_url_prefers_env_ws(self, scraper, monkeypatch):
        monkeypatch.setenv("XUEQIU_CDP_WS_URL", "ws://127.0.0.1:9222/devtools/browser/test")

        assert scraper._resolve_cdp_ws_url() == "ws://127.0.0.1:9222/devtools/browser/test"

    def test_resolve_cdp_ws_url_reads_json_version(self, scraper, monkeypatch):
        monkeypatch.delenv("XUEQIU_CDP_WS_URL", raising=False)
        monkeypatch.setenv("XUEQIU_CDP_HTTP_URL", "http://127.0.0.1:9222")

        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/test"
        }

        with patch("core.xueqiu_scraper.requests.get", return_value=mock_resp) as mock_get:
            assert scraper._resolve_cdp_ws_url() == "ws://127.0.0.1:9222/devtools/browser/test"
            mock_get.assert_called_once_with(
                "http://127.0.0.1:9222/json/version", timeout=2
            )

    def test_resolve_cdp_ws_url_returns_none_when_unavailable(self, scraper, monkeypatch):
        monkeypatch.delenv("XUEQIU_CDP_WS_URL", raising=False)
        monkeypatch.delenv("XUEQIU_CDP_HTTP_URL", raising=False)
        with patch("core.xueqiu_scraper.requests.get", side_effect=OSError("boom")):
            assert scraper._resolve_cdp_ws_url() is None


class TestBrowserSessionOpen:
    def test_open_browser_session_prefers_cdp(self, scraper, monkeypatch):
        monkeypatch.setattr(
            scraper, "_resolve_cdp_ws_url", lambda: "ws://127.0.0.1:9222/devtools/browser/test"
        )
        mock_pw = MagicMock()
        mock_browser = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser.contexts = [mock_context]
        mock_context.new_page.return_value = mock_page
        mock_pw.chromium.connect_over_cdp.return_value = mock_browser

        session = scraper._open_browser_session(mock_pw, headless=False)

        assert session["mode"] == "cdp"
        assert session["page"] is mock_page
        mock_pw.chromium.connect_over_cdp.assert_called_once_with(
            "ws://127.0.0.1:9222/devtools/browser/test"
        )
        mock_pw.chromium.launch_persistent_context.assert_not_called()

    def test_open_browser_session_falls_back_to_launch(self, scraper, monkeypatch):
        monkeypatch.setattr(scraper, "_resolve_cdp_ws_url", lambda: None)
        monkeypatch.setattr(scraper, "_cleanup_stale_profile_locks", MagicMock())
        monkeypatch.setattr(
            scraper,
            "_build_launch_kwargs",
            lambda headless: {"user_data_dir": "x", "headless": headless, "args": []},
        )
        mock_pw = MagicMock()
        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_pw.chromium.launch_persistent_context.return_value = mock_context

        session = scraper._open_browser_session(mock_pw, headless=True)

        assert session["mode"] == "launch"
        assert session["page"] is mock_page
        mock_pw.chromium.launch_persistent_context.assert_called_once()


class TestSyncCursor:
    """Per-user sync cursor CRUD."""

    def test_get_cursor_returns_none_for_new_user(self, scraper):
        assert scraper.db.get_sync_cursor(12345) is None

    def test_set_and_get_cursor(self, scraper):
        cursor = {
            "newest_synced_at": 1709856000000,
            "oldest_synced_at": 1609856000000,
            "next_history_page": 6,
            "total_posts": 100,
            "history_done": False,
            "has_gap": False,
        }
        scraper.db.set_sync_cursor(12345, cursor)
        result = scraper.db.get_sync_cursor(12345)
        assert result == cursor

    def test_set_cursor_overwrites(self, scraper):
        scraper.db.set_sync_cursor(12345, {"newest_synced_at": 100, "oldest_synced_at": 50,
            "next_history_page": 2, "total_posts": 10, "history_done": False, "has_gap": False})
        scraper.db.set_sync_cursor(12345, {"newest_synced_at": 200, "oldest_synced_at": 50,
            "next_history_page": 5, "total_posts": 50, "history_done": False, "has_gap": True})
        result = scraper.db.get_sync_cursor(12345)
        assert result["newest_synced_at"] == 200
        assert result["next_history_page"] == 5
        assert result["has_gap"] is True

    def test_different_users_independent(self, scraper):
        scraper.db.set_sync_cursor(111, {"newest_synced_at": 100, "oldest_synced_at": 50,
            "next_history_page": 2, "total_posts": 10, "history_done": False, "has_gap": False})
        scraper.db.set_sync_cursor(222, {"newest_synced_at": 200, "oldest_synced_at": 80,
            "next_history_page": 8, "total_posts": 80, "history_done": True, "has_gap": False})
        assert scraper.db.get_sync_cursor(111)["next_history_page"] == 2
        assert scraper.db.get_sync_cursor(222)["next_history_page"] == 8


class TestCountPosts:
    def test_count_empty(self, scraper):
        assert scraper.db.count_posts(12345) == 0

    def test_count_after_save(self, scraper):
        scraper.db.save_post({"id": 1, "user_id": 111, "text": "a", "created_at": 1000})
        scraper.db.save_post({"id": 2, "user_id": 111, "text": "b", "created_at": 2000})
        scraper.db.save_post({"id": 3, "user_id": 222, "text": "c", "created_at": 3000})
        assert scraper.db.count_posts(111) == 2
        assert scraper.db.count_posts(222) == 1


class TestSyncAllV2:
    """双阶段增量同步算法测试。"""

    @staticmethod
    def _make_posts(start_id, count, user_id=111, base_ts=2000000000000, ts_step=86400000):
        """生成模拟帖子列表。start_id 最大（最新），id 递减，created_at 也递减。

        ts_step 为正数，created_at = base_ts + id * ts_step，所以 id 越大 created_at 越大。
        """
        return {
            "statuses": [
                {
                    "id": start_id - i,
                    "user_id": user_id,
                    "type": "2",
                    "text": f"<p>Post {start_id - i}</p>",
                    "description": f"Post {start_id - i}",
                    "created_at": base_ts + (start_id - i) * ts_step,
                }
                for i in range(count)
            ]
        }

    def test_first_sync_saves_posts_and_creates_cursor(self, scraper):
        """首次同步：无 cursor，从 page 1 开始，保存帖子并创建 cursor。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        pages = {
            1: self._make_posts(20, 10),
            2: self._make_posts(10, 10),
            3: {"statuses": []},
        }

        def fake_fetch(pg, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"))
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        assert scraper.db.count_posts(111) == 20
        cursor = scraper.db.get_sync_cursor(111)
        assert cursor is not None
        assert cursor["history_done"] is True
        assert cursor["has_gap"] is False

    def test_first_sync_waf_block_raises_clear_error(self, scraper):
        """首次同步第一页一直被 WAF 拦截时，应明确报错而不是误报完成。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        scraper._browser_fetch_api = MagicMock(return_value=None)
        scraper._ensure_waf_ready = MagicMock()

        with pytest.raises(RuntimeError, match="访问验证"):
            scraper._sync_all(mock_page, user_id=111, max_pages=1)

        assert scraper.sync_status == "error"

    def test_incremental_no_new_posts(self, scraper):
        """增量同步：没有新帖，阶段 1 碰到 newest_synced_at 就连上，跳到阶段 2 深挖。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        for i in range(5):
            pid = 20 - i
            scraper.db.save_post({
                "id": pid, "user_id": 111, "text": f"old {pid}",
                "created_at": 2000000000000 + pid * 86400000,
            })
        scraper.db.set_sync_cursor(111, {
            "newest_synced_at": 2000000000000 + 20 * 86400000,
            "oldest_synced_at": 2000000000000 + 16 * 86400000,
            "next_history_page": 2,
            "total_posts": 5,
            "history_done": False,
            "has_gap": False,
        })

        pages = {
            1: self._make_posts(20, 5),
            2: self._make_posts(15, 5),
            3: {"statuses": []},
        }

        def fake_fetch(pg, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"))
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        assert scraper.db.count_posts(111) == 10
        cursor = scraper.db.get_sync_cursor(111)
        assert cursor["history_done"] is True

    def test_incremental_with_new_posts(self, scraper):
        """增量同步：有新帖，阶段 1 追新后连上，阶段 2 继续深挖。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        for i in range(10):
            pid = 20 - i
            scraper.db.save_post({
                "id": pid, "user_id": 111, "text": f"old {pid}",
                "created_at": 2000000000000 + pid * 86400000,
            })
        scraper.db.set_sync_cursor(111, {
            "newest_synced_at": 2000000000000 + 20 * 86400000,
            "oldest_synced_at": 2000000000000 + 11 * 86400000,
            "next_history_page": 3,
            "total_posts": 10,
            "history_done": False,
            "has_gap": False,
        })

        pages = {
            1: self._make_posts(25, 10),  # id 25-16, 5 new + 5 old
            3: self._make_posts(10, 5),   # id 10-6
            4: self._make_posts(5, 5),    # id 5-1
            5: {"statuses": []},
        }

        def fake_fetch(pg, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"), {"statuses": []})
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        cursor = scraper.db.get_sync_cursor(111)
        assert cursor is not None
        assert cursor["newest_synced_at"] == 2000000000000 + 25 * 86400000
        for pid in range(21, 26):
            assert scraper.db.get_post(pid) is not None

    def test_gap_created_when_not_connected(self, scraper):
        """阶段 1 用完配额没连上时，标记 has_gap=True。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        for i in range(10):
            pid = 10 - i
            scraper.db.save_post({
                "id": pid, "user_id": 111, "text": f"old {pid}",
                "created_at": 2000000000000 + pid * 86400000,
            })
        scraper.db.set_sync_cursor(111, {
            "newest_synced_at": 2000000000000 + 10 * 86400000,
            "oldest_synced_at": 2000000000000 + 1 * 86400000,
            "next_history_page": 2,
            "total_posts": 10,
            "history_done": False,
            "has_gap": False,
        })

        pages = {
            1: self._make_posts(70, 20),  # id 70-51
            2: self._make_posts(50, 20),  # id 50-31
        }

        def fake_fetch(pg, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"), {"statuses": []})
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=2)

        cursor = scraper.db.get_sync_cursor(111)
        assert cursor["has_gap"] is True
        assert cursor["newest_synced_at"] == 2000000000000 + 70 * 86400000

    def test_history_done_flag(self, scraper):
        """深挖到空页时标记 history_done=True。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        pages = {
            1: self._make_posts(5, 5),
            2: {"statuses": []},
        }

        def fake_fetch(pg, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"), {"statuses": []})
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        cursor = scraper.db.get_sync_cursor(111)
        assert cursor["history_done"] is True
