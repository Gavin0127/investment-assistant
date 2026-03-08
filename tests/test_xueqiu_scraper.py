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
    """_browser_fetch_api 现在用 page.goto() 导航获取 JSON。"""

    def test_parse_json_response(self, scraper):
        """goto 返回 JSON 时正确解析。"""
        import json as _json
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.evaluate.return_value = _json.dumps({"statuses": [{"id": 1}]})
        mock_page.content.return_value = ""
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result == {"statuses": [{"id": 1}]}

    def test_detect_waf_html(self, scraper):
        """goto 返回 WAF challenge 页面时识别为拦截。"""
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.evaluate.return_value = ""  # body.innerText 为空
        mock_page.content.return_value = '<meta name="aliyun_waf_aa">'
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result is None

    def test_detect_empty_response(self, scraper):
        """goto 返回空内容时识别为失败。"""
        mock_page = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_page.goto.return_value = mock_resp
        mock_page.evaluate.return_value = ""
        mock_page.content.return_value = ""
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result is None
