"""Tests for XueqiuDB."""

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture()
def db(tmp_path):
    from core.xueqiu_db import XueqiuDB
    return XueqiuDB(str(tmp_path / "test.db"))


class TestSchema:
    def test_tables_created(self, db):
        conn = sqlite3.connect(db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "posts" in tables
        assert "images" in tables
        assert "sync_state" in tables
        assert "posts_fts" in tables
        conn.close()


class TestSaveAndGet:
    def test_save_and_get_post(self, db):
        post = {
            "id": 12345,
            "user_id": 1936609590,
            "type": "2",
            "is_column": False,
            "title": None,
            "text": "<p>测试内容</p>",
            "description": "测试内容",
            "created_at": 1709856000000,
            "edited_at": None,
            "target": "/1936609590/12345",
            "retweet_status_id": 0,
            "retweet_text": None,
            "reply_count": 5,
            "like_count": 10,
            "retweet_count": 3,
            "view_count": 100,
        }
        db.save_post(post)
        result = db.get_post(12345)
        assert result is not None
        assert result["id"] == 12345
        assert result["text"] == "<p>测试内容</p>"
        assert result["like_count"] == 10

    def test_save_post_upsert(self, db):
        post = {
            "id": 12345, "user_id": 1936609590, "type": "2",
            "text": "v1", "description": "v1", "created_at": 1709856000000,
        }
        db.save_post(post)
        post["text"] = "v2"
        post["like_count"] = 99
        db.save_post(post)
        result = db.get_post(12345)
        assert result["text"] == "v2"
        assert result["like_count"] == 99

    def test_save_and_get_image(self, db):
        post = {
            "id": 100, "user_id": 1, "type": "2",
            "text": "img", "description": "img", "created_at": 1000,
        }
        db.save_post(post)
        db.save_image(100, "https://cdn.example.com/a.jpg", "/local/a.jpg", 0)
        db.save_image(100, "https://cdn.example.com/b.jpg", "/local/b.jpg", 1)
        images = db.get_images(100)
        assert len(images) == 2
        assert images[0]["original_url"] == "https://cdn.example.com/a.jpg"
        assert images[1]["seq"] == 1


class TestListPosts:
    @pytest.fixture(autouse=True)
    def seed(self, db):
        for i in range(5):
            db.save_post({
                "id": i + 1, "user_id": 1, "type": "2" if i % 2 == 0 else "0",
                "is_column": i == 4,
                "title": f"Title {i}" if i == 4 else None,
                "text": f"Content {i}", "description": f"Desc {i}",
                "created_at": 1000 + i * 1000,
                "retweet_status_id": 100 if i == 3 else 0,
            })

    def test_list_all(self, db):
        posts, total = db.list_posts(page=1, per_page=10)
        assert total == 5
        assert len(posts) == 5
        assert posts[0]["id"] == 5  # 按 created_at 倒序

    def test_list_pagination(self, db):
        posts, total = db.list_posts(page=1, per_page=2)
        assert total == 5
        assert len(posts) == 2

    def test_filter_original(self, db):
        posts, total = db.list_posts(post_type="original")
        assert all(p["retweet_status_id"] == 0 for p in posts)

    def test_filter_retweet(self, db):
        posts, total = db.list_posts(post_type="retweet")
        assert total == 1
        assert posts[0]["retweet_status_id"] == 100

    def test_filter_column(self, db):
        posts, total = db.list_posts(post_type="column")
        assert total == 1
        assert posts[0]["is_column"] == 1

    def test_filter_by_user_id(self, db):
        db.save_post({
            "id": 100, "user_id": 999, "type": "2",
            "text": "Other user", "description": "Other",
            "created_at": 9000,
        })
        posts, total = db.list_posts(user_id=999)
        assert total == 1
        assert posts[0]["user_id"] == 999


class TestFTS:
    def test_search(self, db):
        db.save_post({
            "id": 1, "user_id": 1, "type": "2",
            "title": "铜价分析", "text": "铜价创新高的背后逻辑",
            "description": "铜价", "created_at": 1000,
        })
        db.save_post({
            "id": 2, "user_id": 1, "type": "0",
            "text": "今天天气不错", "description": "天气",
            "created_at": 2000,
        })
        posts, total = db.list_posts(query="铜价")
        assert total == 1
        assert posts[0]["id"] == 1


class TestSyncState:
    def test_get_set(self, db):
        assert db.get_sync_state("last_post_id") is None
        db.set_sync_state("last_post_id", "99999")
        assert db.get_sync_state("last_post_id") == "99999"
        db.set_sync_state("last_post_id", "100000")
        assert db.get_sync_state("last_post_id") == "100000"


class TestUserManagement:
    def test_add_and_get_user(self, db):
        db.add_user(1936609590, "逸修1")
        user = db.get_user(1936609590)
        assert user is not None
        assert user["nickname"] == "逸修1"
        assert user["is_active"] == 1

    def test_add_duplicate_user(self, db):
        db.add_user(1936609590, "逸修1")
        db.add_user(1936609590, "逸修1改名")
        user = db.get_user(1936609590)
        assert user["nickname"] == "逸修1改名"

    def test_list_users(self, db):
        db.add_user(1, "用户A")
        db.add_user(2, "用户B")
        users = db.list_users()
        assert len(users) == 2

    def test_remove_user(self, db):
        db.add_user(1, "用户A")
        db.remove_user(1)
        assert db.get_user(1) is None

    def test_list_users_empty(self, db):
        assert db.list_users() == []

    def test_xueqiu_users_table_created(self, db):
        import sqlite3
        conn = sqlite3.connect(db.db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "xueqiu_users" in tables
        conn.close()
