# 雪球增量同步 V2 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现双阶段增量同步 — 每次同步 N 页时，先追新帖，再从历史断点继续深挖，逐步同步完整 timeline。

**Architecture:** Per-user sync cursor 存储在 sync_state 表中（JSON），记录 newest/oldest_synced_at、next_history_page、has_gap。同步分两阶段：阶段 1 从 page 1 追新并 upsert 更新统计，阶段 2 跳到历史断点继续深挖。

**Tech Stack:** Python 3.10+, SQLite, pytest, Flask, Alpine.js

**Design doc:** `docs/plans/2026-03-09-xueqiu-incremental-sync-design.md`

---

### Task 1: XueqiuDB — 添加 per-user cursor 和 count_posts 方法

**Files:**
- Modify: `core/xueqiu_db.py:251-268` (在 `set_sync_state` 之后添加)
- Test: `tests/test_xueqiu_scraper.py`

**Step 1: 写失败测试**

在 `tests/test_xueqiu_scraper.py` 末尾添加：

```python
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
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestSyncCursor -v`
Expected: FAIL — `AttributeError: 'XueqiuDB' object has no attribute 'get_sync_cursor'`

**Step 3: 实现**

在 `core/xueqiu_db.py` 的 `set_sync_state` 方法之后（约 line 269）添加：

```python
    def get_sync_cursor(self, user_id: int) -> Optional[dict]:
        """Get per-user sync cursor. Returns None if never synced."""
        import json
        raw = self.get_sync_state(f"sync_cursor:{user_id}")
        if raw is None:
            return None
        return json.loads(raw)

    def set_sync_cursor(self, user_id: int, cursor: dict) -> None:
        """Save per-user sync cursor."""
        import json
        self.set_sync_state(f"sync_cursor:{user_id}", json.dumps(cursor))

    def count_posts(self, user_id: int) -> int:
        """Count posts for a specific user."""
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM posts WHERE user_id = ?", (user_id,)
            ).fetchone()
            return row[0]
```

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestSyncCursor tests/test_xueqiu_scraper.py::TestCountPosts -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add core/xueqiu_db.py tests/test_xueqiu_scraper.py
git commit -m "feat(xueqiu): add per-user sync cursor and count_posts to XueqiuDB"
```

---

### Task 2: 重写 _sync_all — 双阶段增量同步核心算法

**Files:**
- Modify: `core/xueqiu_scraper.py:109-202`
- Test: `tests/test_xueqiu_scraper.py`

**Step 1: 写失败测试**

在 `tests/test_xueqiu_scraper.py` 末尾添加。这些测试 mock 掉浏览器交互，只测试同步逻辑。

```python
class TestSyncAllV2:
    """双阶段增量同步算法测试。

    mock _browser_fetch_api 返回预设数据，mock _ensure_waf_ready 为空操作，
    mock page.wait_for_timeout 为空操作。只测试同步逻辑本身。
    """

    @staticmethod
    def _make_posts(start_id, count, user_id=111, base_ts=1700000000000, ts_step=-86400000):
        """生成模拟帖子列表。start_id 最大（最新），id 递减，created_at 也递减。"""
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
            1: self._make_posts(20, 10, base_ts=2000000000000, ts_step=86400000),
            2: self._make_posts(10, 10, base_ts=2000000000000, ts_step=86400000),
            3: {"statuses": []},  # 空页，到底了
        }

        def fake_fetch(page, path, params):
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

    def test_incremental_no_new_posts(self, scraper):
        """增量同步：没有新帖，阶段 1 碰到 newest_synced_at 就连上，跳到阶段 2 深挖。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        # 预存 5 条帖子（id 16-20），模拟首次同步了 1 页
        for i in range(5):
            scraper.db.save_post({
                "id": 20 - i, "user_id": 111, "text": f"old {20-i}",
                "created_at": 2000000000000 + (20 - i) * 86400000,
            })
        scraper.db.set_sync_cursor(111, {
            "newest_synced_at": 2000000000000 + 20 * 86400000,
            "oldest_synced_at": 2000000000000 + 16 * 86400000,
            "next_history_page": 2,
            "total_posts": 5,
            "history_done": False,
            "has_gap": False,
        })

        # page 1 返回同样的帖子（已同步），page 2 返回新的历史帖子
        pages = {
            1: self._make_posts(20, 5, base_ts=2000000000000, ts_step=86400000),
            2: self._make_posts(15, 5, base_ts=2000000000000, ts_step=86400000),
            3: {"statuses": []},
        }

        def fake_fetch(page, path, params):
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

        # 预存帖子 id 11-20
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

        # 用户发了 5 条新帖 (id 21-25)，page 1 有新帖+旧帖混合
        pages = {
            1: self._make_posts(25, 10, base_ts=2000000000000, ts_step=86400000),  # id 25-16
            # page 1 最老帖子 id=16, created_at 在已同步区内 → 连上
            # 阶段 2: 跳到 next_history_page=3, 但新帖偏移了，调整后从 page 3 或 4 开始
            3: self._make_posts(10, 5, base_ts=2000000000000, ts_step=86400000),  # id 10-6
            4: self._make_posts(5, 5, base_ts=2000000000000, ts_step=86400000),   # id 5-1
            5: {"statuses": []},
        }

        def fake_fetch(page_obj, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"), {"statuses": []})
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        cursor = scraper.db.get_sync_cursor(111)
        assert cursor is not None
        assert cursor["newest_synced_at"] == 2000000000000 + 25 * 86400000
        # 新帖 21-25 应该被保存
        for pid in range(21, 26):
            assert scraper.db.get_post(pid) is not None

    def test_gap_created_when_not_connected(self, scraper):
        """阶段 1 用完配额没连上时，标记 has_gap=True。"""
        mock_page = MagicMock()
        mock_page.wait_for_timeout = MagicMock()

        # 预存帖子 id 1-10
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

        # 用户发了 60 条新帖 (id 11-70)，max_pages=2 不够连上
        pages = {
            1: self._make_posts(70, 20, base_ts=2000000000000, ts_step=86400000),  # id 70-51
            2: self._make_posts(50, 20, base_ts=2000000000000, ts_step=86400000),  # id 50-31
            # 最老 id=31, created_at > newest_synced_at(id=10) → 没连上
        }

        def fake_fetch(page_obj, path, params):
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
            1: self._make_posts(5, 5, base_ts=2000000000000, ts_step=86400000),
            2: {"statuses": []},
        }

        def fake_fetch(page_obj, path, params):
            if path == "/v4/statuses/user_timeline.json":
                return pages.get(params.get("page"), {"statuses": []})
            return None

        scraper._browser_fetch_api = fake_fetch
        scraper._ensure_waf_ready = MagicMock()

        scraper._sync_all(mock_page, user_id=111, max_pages=5)

        cursor = scraper.db.get_sync_cursor(111)
        assert cursor["history_done"] is True
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestSyncAllV2::test_first_sync_saves_posts_and_creates_cursor -v`
Expected: FAIL (current `_sync_all` doesn't create cursor)

**Step 3: 实现 — 重写 `_sync_all`**

Replace `core/xueqiu_scraper.py` lines 109-202 (the entire `_sync_all` method) with the new dual-phase implementation:

```python
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
        all_timestamps: list[int] = []  # 收集所有帖子的 created_at
        waf_retries = 0

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

                    # 判断是否连上已同步区
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

                # 更新 newest_synced_at
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
                start_page = 1
            else:
                # 修正页码偏移：新帖数 / 每页条数（雪球默认每页 20 条）
                page_offset = new_count // 20
                start_page = cursor.get("next_history_page", 1) + page_offset

            pg = start_page
            self.sync_progress = "正在定位历史断点..."
            _log(f"Phase 2: starting at page {pg} (remaining={remaining})")

            # 定位：跳过已同步页（最多 3 次尝试）
            locate_attempts = 0
            while locate_attempts < 3 and remaining > 0:
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

                new_on_page = sum(
                    1 for p in posts if not self.db.get_post(p["id"])
                )
                if new_on_page > 0:
                    _log(f"Phase 2: found {new_on_page} new posts at page {pg}")
                    break  # 找到有新帖的页，开始深挖

                # 整页已同步，upsert 更新统计，继续找
                _log(f"Phase 2: page {pg} all synced, locating...")
                page_new, page_updated = self._process_posts(
                    page, posts, pending_images, all_timestamps
                )
                updated_count += page_updated
                pg += 1
                locate_attempts += 1
                remaining -= 1
                page.wait_for_timeout(5000)

            # 从定位到的页开始深挖
            while remaining > 0:
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

            # 更新 cursor
            if cursor is None:
                cursor = self._make_cursor(all_timestamps)
            if all_timestamps:
                cursor["newest_synced_at"] = max(
                    cursor.get("newest_synced_at", 0), max(all_timestamps)
                )
                cursor["oldest_synced_at"] = min(
                    cursor.get("oldest_synced_at", float("inf")),
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
        for attempt in range(4):  # 1 normal + 3 retries
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
        self.db.set_sync_state("total_synced", str(new_count))
        self.sync_status = "done"
        self.sync_progress = f"同步完成：{new_count} 条新帖，{updated_count} 条更新"
        _log(f"Sync complete: {new_count} new, {updated_count} updated, {len(pending_images)} images")
```

Also remove the now-unused `get_latest_post_id` method from `core/xueqiu_db.py` (lines 298-304) since the new algorithm uses `get_sync_cursor` instead.

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add core/xueqiu_scraper.py core/xueqiu_db.py tests/test_xueqiu_scraper.py
git commit -m "feat(xueqiu): rewrite _sync_all with dual-phase incremental sync"
```

---

### Task 3: 更新 sync status API 返回 per-user cursor 信息

**Files:**
- Modify: `web/app.py:1053-1064`

**Step 1: 修改 sync status API**

将 `api_xueqiu_sync_status` 路由（`web/app.py:1053-1064`）改为：

```python
@app.route('/api/xueqiu/sync/status', methods=['GET'])
@requires_auth
def api_xueqiu_sync_status():
    scraper = _get_xueqiu_scraper()
    db = _get_xueqiu_db()
    last_sync = db.get_sync_state("last_sync_time")
    # 如果请求带了 user_id，返回 per-user cursor 信息
    user_id = request.args.get("user_id", type=int)
    cursor = db.get_sync_cursor(user_id) if user_id else None
    return jsonify({
        "status": scraper.sync_status,
        "progress": scraper.sync_progress,
        "count": scraper.sync_count,
        "last_sync_time": last_sync,
        "cursor": cursor,
    })
```

**Step 2: 手动验证**

Run: `uv run python -c "from web.app import app; print('import ok')"`
Expected: `import ok`（无语法错误）

**Step 3: 提交**

```bash
git add web/app.py
git commit -m "feat(xueqiu): return per-user sync cursor in status API"
```

---

### Task 4: 前端同步状态展示优化

**Files:**
- Modify: `web/templates/xueqiu.html`

**Step 1: 更新 fetchSyncStatus 和 pollSyncStatus**

在 `web/templates/xueqiu.html` 的 `xueqiuApp()` 中：

1. 添加 `syncCursor` 状态变量（在 `showSyncPanel: false,` 之后）：

```javascript
    syncCursor: null,
```

2. 修改 `fetchSyncStatus` 方法，传入 user_id 并保存 cursor：

```javascript
    async fetchSyncStatus() {
      try {
        const params = this.selectedUserId ? `?user_id=${this.selectedUserId}` : '';
        const resp = await fetch('/api/xueqiu/sync/status' + params);
        const data = await resp.json();
        if (data.last_sync_time) {
          const d = new Date(parseInt(data.last_sync_time) * 1000);
          this.lastSyncTime = d.toLocaleString('zh-CN');
        }
        this.syncCursor = data.cursor;
      } catch (e) { /* ignore */ }
    },
```

3. 修改 `pollSyncStatus` 方法，传入 user_id：

```javascript
    pollSyncStatus() {
      const poll = setInterval(async () => {
        try {
          const params = this.selectedUserId ? `?user_id=${this.selectedUserId}` : '';
          const resp = await fetch('/api/xueqiu/sync/status' + params);
          const data = await resp.json();
          if (data.status === 'syncing' || data.status === 'logging_in') {
            this.syncProgress = data.progress || ('已同步 ' + (data.count || 0) + ' 条');
          } else {
            clearInterval(poll);
            this.syncing = false;
            if (data.status === 'error') {
              this.syncProgress = '';
              alert('同步失败: ' + (data.progress || '未知错误'));
            } else {
              this.syncProgress = '';
              if (data.last_sync_time) {
                const d = new Date(parseInt(data.last_sync_time) * 1000);
                this.lastSyncTime = d.toLocaleString('zh-CN');
              }
              this.syncCursor = data.cursor;
              this.resetAndLoad();
            }
          }
        } catch (e) {
          clearInterval(poll);
          this.syncing = false;
          this.syncProgress = '';
        }
      }, 2000);
    },
```

4. 修改同步状态栏（底部），替换原来的 `lastSyncTime` 显示：

将：
```html
      <div x-show="lastSyncTime" class="px-3 py-2 border-t border-gray-100 flex-shrink-0">
        <span class="text-xs text-gray-400" x-text="'上次同步: ' + lastSyncTime"></span>
      </div>
```

替换为：
```html
      <div x-show="lastSyncTime || syncCursor" class="px-3 py-2 border-t border-gray-100 flex-shrink-0">
        <div class="flex items-center gap-2 text-xs text-gray-400 flex-wrap">
          <span x-show="syncCursor" x-text="'已同步 ' + (syncCursor?.total_posts || 0) + ' 条'"></span>
          <span x-show="syncCursor && syncCursor.history_done" class="text-green-500">历史已全部同步</span>
          <span x-show="syncCursor && !syncCursor.history_done" class="text-amber-500">历史同步中</span>
          <span x-show="lastSyncTime" x-text="'上次: ' + lastSyncTime"></span>
        </div>
      </div>
```

5. 修改同步按钮文案，根据 cursor 状态显示不同文案：

将：
```html
          <span x-show="!syncing">同步数据 ▾</span>
```

替换为：
```html
          <span x-show="!syncing" x-text="!syncCursor ? '开始同步 ▾' : syncCursor.history_done ? '更新数据 ▾' : '继续同步 ▾'"></span>
```

6. 在 `resetAndLoad` 方法末尾调用 `fetchSyncStatus`：

```javascript
    resetAndLoad() {
      this.page = 1;
      this.hasMore = true;
      this.loadPosts(false);
      this.fetchSyncStatus();
    },
```

**Step 2: 手动验证**

在浏览器中打开 http://localhost:8100/xueqiu，确认：
- 选择用户后底部显示同步状态
- 按钮文案根据状态变化
- 同步后进度正确更新

**Step 3: 提交**

```bash
git add web/templates/xueqiu.html
git commit -m "feat(xueqiu): show per-user sync progress and cursor status in UI"
```

---

### Task 5: 清理旧代码 + 全量测试

**Files:**
- Modify: `core/xueqiu_db.py` (删除 `get_latest_post_id`)
- Modify: `tests/test_xueqiu_scraper.py` (确保无引用)

**Step 1: 检查 get_latest_post_id 是否还有引用**

Run: `grep -rn "get_latest_post_id" core/ web/ tests/`

如果只在 `xueqiu_db.py` 的定义处出现，删除该方法（lines 298-304）。

**Step 2: 运行全量测试**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

**Step 3: 提交**

```bash
git add -A
git commit -m "refactor(xueqiu): remove unused get_latest_post_id"
```

---

### Task 6: 端到端验证

**不写代码，只做验证。**

**Step 1: 启动应用**

用户手动运行: `uv run python web/app.py`

**Step 2: 验证首次同步**

1. 打开 http://localhost:8100/xueqiu
2. 选择一个用户
3. 设置同步页数为 2，点击"开始同步"
4. 确认：
   - 进度显示"正在同步历史帖子..."
   - 完成后底部显示"已同步 X 条 | 历史同步中"
   - 帖子列表正确加载

**Step 3: 验证增量同步**

1. 再次点击"继续同步"，页数设为 2
2. 确认：
   - 进度显示"正在检查新帖..."
   - 然后跳到"正在同步历史帖子 (第 X 页)"
   - 完成后帖子总数增加
   - 底部状态更新

**Step 4: 验证历史全部同步**

1. 设置较大页数（如 50），点击"继续同步"
2. 多次同步直到底部显示"历史已全部同步"
3. 确认按钮文案变为"更新数据"
