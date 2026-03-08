"""Tests for xueqiu API routes."""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def app_client(tmp_path):
    """Create a Flask test client with tmp storage."""
    import web.app as webapp
    from core.storage import Storage

    webapp.storage = Storage(base_dir=str(tmp_path / "inv"))

    # Create a test DB with some posts
    db_path = tmp_path / "inv" / "data" / "xueqiu_posts.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    from core.xueqiu_db import XueqiuDB
    db = XueqiuDB(str(db_path))
    for i in range(3):
        db.save_post({
            "id": i + 1, "user_id": 1936609590, "type": "2",
            "title": f"Title {i}" if i == 0 else None,
            "text": f"<p>Content {i} copper analysis</p>" if i == 0 else f"<p>Content {i}</p>",
            "description": f"Desc {i}",
            "created_at": 1709856000000 + i * 86400000,
            "retweet_status_id": 100 if i == 2 else 0,
        })

    # Reset global scraper and db singleton so it picks up new paths
    webapp._xueqiu_scraper = None
    webapp._xueqiu_db = None

    webapp.app.config["TESTING"] = True
    with webapp.app.test_client() as client:
        yield client, db


class TestListPosts:
    def test_list_all(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total"] == 3
        assert len(data["posts"]) == 3

    def test_pagination(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts?per_page=2&page=1")
        data = resp.get_json()
        assert len(data["posts"]) == 2
        assert data["total"] == 3

    def test_search(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts?q=copper")
        data = resp.get_json()
        assert data["total"] == 1

    def test_filter_retweet(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts?type=retweet")
        data = resp.get_json()
        assert data["total"] == 1


class TestGetPost:
    def test_get_existing(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts/1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["id"] == 1

    def test_get_missing(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/posts/999")
        assert resp.status_code == 404

    def test_get_post_images_format(self, app_client):
        """images 应返回文件名字符串列表，而非 dict 列表。"""
        client, db = app_client
        db.save_post({"id": 100, "user_id": 1, "type": "2",
                       "text": "test", "created_at": 1000})
        db.save_image(100, "https://example.com/a.jpg",
                       "/path/to/images/100/000.jpg", 0)
        resp = client.get("/api/xueqiu/posts/100")
        data = resp.get_json()
        assert isinstance(data["images"], list)
        assert data["images"][0] == "000.jpg"


class TestSyncStatus:
    def test_idle(self, app_client):
        client, db = app_client
        resp = client.get("/api/xueqiu/sync/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "idle"
