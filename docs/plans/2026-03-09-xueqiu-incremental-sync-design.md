# 雪球增量同步 V2 设计

## 问题

当前同步每次从第 1 页开始，碰到已有帖子就停。这导致：

1. 无法逐步深挖历史 — 用户选"同步 5 页"，但如果前 5 页已同步过，立即停止，历史永远推不下去
2. 已有帖子不更新 — 点赞数、转发数等统计数据永远停留在首次同步时的值
3. 同步状态是全局的 — 不区分用户，多用户场景下状态混乱

## 目标

用户每次点"同步 N 页"时：

1. 先从第 1 页扫描，拿到最新帖子，同时 upsert 更新已有帖子的统计数据
2. 扫到和已同步区域连上后，跳过已同步区域，从历史断点继续往后翻 N 页
3. 如果新内容太多（超过 N 页还没连上），下次同步继续补

最终效果：多次同步后，整个 timeline 从新到旧完整同步。

## 数据模型

### Per-User Sync Cursor

在 `sync_state` 表中存储 per-user 的同步游标，key 为 `sync_cursor:{user_id}`，value 为 JSON：

```json
{
  "newest_synced_at": 1709856000000,
  "oldest_synced_at": 1609856000000,
  "next_history_page": 15,
  "total_posts": 280,
  "history_done": false
}
```

字段说明：

- `newest_synced_at`：已同步的最新帖子的 `created_at`（毫秒时间戳）
- `oldest_synced_at`：已同步的最老帖子的 `created_at`（毫秒时间戳）
- `next_history_page`：下次深挖历史应从哪页开始
- `total_posts`：该用户已同步的帖子总数
- `history_done`：是否已同步到 timeline 末尾（拉到空页）

## 同步算法

### 概览

```
┌─────────────────────────────────────────────────┐
│              雪球 Timeline（时间倒序）              │
│                                                   │
│  Page 1  [最新帖子]                                │
│  Page 2  [较新帖子]                                │
│  ...                                              │
│  Page K  ← newest_synced_at 在这附近               │
│  Page K+1 到 Page M  [已同步区域]                   │
│  Page M  ← oldest_synced_at 在这附近               │
│  Page M+1 ← next_history_page（深挖起点）           │
│  ...                                              │
│  Page N  [最老帖子]                                │
└─────────────────────────────────────────────────┘
```

### 阶段 1：追新（从 page 1 开始）

从第 1 页开始逐页翻，每条帖子都 upsert（新帖插入，旧帖更新统计数据）。

判断"连上已同步区"的条件：当前页的最老帖子 `created_at <= newest_synced_at`。

连上后进入阶段 2。如果翻了 N 页还没连上，停止，更新 `newest_synced_at` 为本次最新帖子的时间戳，`next_history_page` 不变。下次同步继续从 page 1 补。

首次同步（无 cursor）：跳过阶段 1，直接进入阶段 2。

### 阶段 2：深挖历史（从断点跳转）

连上已同步区后，跳到 `next_history_page` 继续往后翻。

跳转前需要修正页码偏移。新帖会把所有旧帖往后推，修正公式：

```python
# 阶段 1 中发现的新帖数量
new_posts_in_phase1 = count of posts with created_at > newest_synced_at
page_offset = new_posts_in_phase1 // page_size
adjusted_page = next_history_page + page_offset
```

跳到 `adjusted_page` 后：

- 如果整页都是已同步帖子 → 页码 +1，继续找（最多尝试 3 次）
- 找到有新帖的页 → 从该页开始逐页翻，每条 upsert
- 翻到空页 → 标记 `history_done = true`

阶段 2 消耗剩余配额（N - 阶段 1 消耗的页数）。

### 特殊情况：已同步区中间有 gap

当阶段 1 用完配额但没连上已同步区时，会产生 gap：

```
[本次新同步的区域] --- gap --- [历史已同步区域]
```

下次同步时，阶段 1 从 page 1 开始，会先穿过"本次新同步的区域"（这些帖子已存在，upsert 更新），然后进入 gap 区域（新帖子，插入），最终连上历史已同步区域。

判断"连上"用的是 `oldest_synced_at`（不是 `newest_synced_at`），这样能确保 gap 被完整填补。

等等 — 这里需要修正。阶段 1 的连上条件应该分两层：

1. 碰到 `newest_synced_at` → 进入已同步区，但可能有 gap
2. 碰到 `oldest_synced_at` → 穿过已同步区，可以跳到 `next_history_page`

如果在 `newest_synced_at` 和 `oldest_synced_at` 之间有 gap，需要继续翻页填补，不能跳。

修正后的阶段 1 逻辑：

```
从 page 1 开始翻：
  - 每页 upsert 所有帖子
  - 当页最老帖子 created_at <= oldest_synced_at → 连上，进入阶段 2
  - 当页最老帖子 created_at <= newest_synced_at 但 > oldest_synced_at → 在已同步区内但还没穿过，继续翻
  - 用完 N 页配额还没连上 → 停止，记录进度
```

但这样如果已同步区很大（比如 50 页），阶段 1 会浪费大量配额在逐页翻已同步区上。

优化：当碰到 `newest_synced_at` 后，检查是否存在 gap。如果不存在 gap（即上次同步时阶段 1 已经连上了），直接跳到 `next_history_page`。

如何判断是否存在 gap？在 cursor 中增加一个字段 `has_gap`：

```json
{
  "newest_synced_at": 1709856000000,
  "oldest_synced_at": 1609856000000,
  "next_history_page": 15,
  "total_posts": 280,
  "history_done": false,
  "has_gap": false
}
```

- 阶段 1 连上 `oldest_synced_at` → `has_gap = false`
- 阶段 1 用完配额没连上 → `has_gap = true`

下次同步时：
- `has_gap = false` → 碰到 `newest_synced_at` 就直接跳到 `next_history_page`
- `has_gap = true` → 碰到 `newest_synced_at` 后继续翻，直到连上 `oldest_synced_at` 或用完配额

### 完整算法伪代码

```python
def sync(user_id, max_pages):
    cursor = load_cursor(user_id)  # 可能为 None（首次同步）
    pages_used = 0
    new_count = 0
    updated_count = 0

    # === 阶段 1：追新 ===
    if cursor is not None:
        pg = 1
        connected = False

        while pages_used < max_pages:
            posts = fetch_page(user_id, pg)
            if not posts:
                break
            pages_used += 1

            page_oldest_at = min(p.created_at for p in posts)

            for post in posts:
                existing = db.get_post(post.id)
                db.save_post(post)  # upsert
                if existing:
                    updated_count += 1
                else:
                    new_count += 1

            # 判断是否连上
            if not cursor.has_gap:
                # 无 gap：碰到 newest_synced_at 就够了
                if page_oldest_at <= cursor.newest_synced_at:
                    connected = True
                    break
            else:
                # 有 gap：必须穿过到 oldest_synced_at
                if page_oldest_at <= cursor.oldest_synced_at:
                    connected = True
                    cursor.has_gap = False
                    break

            pg += 1

        # 更新 newest
        cursor.newest_synced_at = max(
            cursor.newest_synced_at,
            max(p.created_at for p in all_phase1_posts)
        )

        if not connected:
            # 没连上，标记 gap，下次继续
            cursor.has_gap = True
            save_cursor(cursor)
            return

    # === 阶段 2：深挖历史 ===
    remaining = max_pages - pages_used
    if remaining <= 0:
        save_cursor(cursor)
        return

    if cursor is None:
        # 首次同步，从 page 1 开始
        start_page = 1
    else:
        # 修正页码偏移
        page_offset = new_count // PAGE_SIZE
        start_page = cursor.next_history_page + page_offset

    pg = start_page
    # 跳转后定位：跳过已同步页（最多 3 次）
    locate_attempts = 0
    while locate_attempts < 3:
        posts = fetch_page(user_id, pg)
        if not posts:
            cursor.history_done = True
            break

        new_on_page = sum(1 for p in posts if not db.get_post(p.id))
        if new_on_page > 0:
            break  # 找到有新帖的页

        # 整页已同步，继续找
        for post in posts:
            db.save_post(post)  # upsert 更新统计
            updated_count += 1
        pg += 1
        locate_attempts += 1
        remaining -= 1  # 定位也消耗配额

    # 从定位到的页开始深挖
    while remaining > 0:
        posts = fetch_page(user_id, pg)
        if not posts:
            cursor.history_done = True
            break
        remaining -= 1

        for post in posts:
            existing = db.get_post(post.id)
            db.save_post(post)
            if existing:
                updated_count += 1
            else:
                new_count += 1

        pg += 1

    # 更新 cursor
    if cursor is None:
        cursor = SyncCursor()
        cursor.newest_synced_at = max(p.created_at for p in all_posts)

    cursor.oldest_synced_at = min(
        cursor.oldest_synced_at or float('inf'),
        min(p.created_at for p in all_phase2_posts)
    )
    cursor.next_history_page = pg
    cursor.total_posts = db.count_posts(user_id)
    save_cursor(cursor)
```

## 进度显示

同步过程中的进度文案：

| 阶段 | 进度文案 |
|------|---------|
| 阶段 1 追新 | `"正在检查新帖... (第 {pg} 页)"` |
| 阶段 1 连上 | `"已追上最新，发现 {new_count} 条新帖"` |
| 阶段 1 未连上 | `"发现 {new_count} 条新帖，下次继续补全"` |
| 阶段 2 定位 | `"正在定位历史断点..."` |
| 阶段 2 深挖 | `"正在同步历史帖子 (第 {pg} 页)，已保存 {total} 条"` |
| 阶段 2 到底 | `"历史帖子已全部同步"` |
| 完成 | `"同步完成：{new_count} 条新帖，{updated_count} 条更新"` |

## 前端变更

### 同步状态展示

在同步状态区域增加 per-user 的同步信息：

```
已同步 280 条帖子 | 历史进度: 15/~25 页 | 上次同步: 2026-03-09 14:30
```

`history_done` 为 true 时显示"历史已全部同步"。

### 同步按钮文案

- 首次同步：`"开始同步"`
- 增量同步：`"继续同步"`
- 历史已全部同步：`"更新数据"`（只跑阶段 1）

## 需要修改的文件

| 文件 | 变更 |
|------|------|
| `core/xueqiu_db.py` | 新增 `get_sync_cursor(user_id)`, `set_sync_cursor(user_id, cursor)`, `count_posts(user_id)` |
| `core/xueqiu_scraper.py` | 重写 `_sync_all()` 为双阶段算法 |
| `web/templates/xueqiu.html` | 同步状态展示优化，按钮文案 |
| `web/app.py` | sync status API 返回 per-user cursor 信息 |
| `tests/test_xueqiu_scraper.py` | 新增增量同步场景测试 |

## 向后兼容

首次运行新代码时，已有的全局 `sync_state` 数据保留不动。per-user cursor 不存在时视为首次同步，从 page 1 开始。旧的全局 `last_sync_time` 和 `total_synced` 继续写入，保持前端兼容。
