# 雪球内容爬取与展示

## 目标

爬取雪球用户的全部动态（原创 + 转发），存储到本地 SQLite，通过双栏页面浏览和搜索。

## 背景

目标用户页面：`https://xueqiu.com/u/1936609590`（逸修1），约 182 页、3800 条动态。雪球 API 未登录仅返回第 1 页，全量拉取需要登录态。

## 技术选型

爬虫框架选用 [Scrapling](https://github.com/D4Vinci/Scrapling)：
- `StealthyFetcher`：Playwright 隐身模式，内置反检测，用于登录阶段
- `Fetcher`：纯 HTTP + TLS 指纹模拟，登录后切换到此模式加速爬取
- 内置反爬机制（TLS 指纹、请求头伪装、WebRTC 防护），无需额外处理

## 爬取流程

```
StealthyFetcher 打开雪球（headless=False）
    ↓
用户在弹出的浏览器中手动登录
    ↓
检测 Cookie 中 u 字段 → 登录成功
    ↓
提取 Cookie，切换到 Fetcher（纯 HTTP）
    ↓
逐页调用 /v4/statuses/user_timeline.json
    ↓
type=3 长文章 → 调用 /statuses/show.json 获取全文
    ↓
解析 HTML 中 <img>，下载图片到本地
    ↓
写入 SQLite + 更新 FTS 索引
```

### 增量更新

每次爬取前查 DB 中最新帖子的 `created_at`。从第 1 页开始拉取，遇到已存在的帖子则停止翻页。

### 雪球 API

| 端点 | 用途 |
|------|------|
| `GET /v4/statuses/user_timeline.json?page=N&user_id={uid}` | 全部动态，每页约 21 条 |
| `GET /statuses/show.json?id={status_id}` | 单个帖子完整内容 |

### 帖子类型

| type | 含义 | timeline 中有全文 |
|------|------|-------------------|
| `"3"` (is_column=true) | 长文/专栏 | 否，text 为空，需单独获取 |
| `"2"` | 带图/中等帖子 | 是 |
| `"0"` | 短帖/状态 | 是 |
| null (retweet_status_id≠0) | 转发 | 是（含原文） |

## 数据模型

SQLite 数据库：`~/.investment-assistant/data/xueqiu_posts.db`

图片目录：`~/.investment-assistant/data/xueqiu_images/{post_id}/{filename}`

```sql
CREATE TABLE posts (
    id                INTEGER PRIMARY KEY,  -- 雪球帖子 ID
    user_id           INTEGER NOT NULL,
    type              TEXT,                 -- "0"/"2"/"3"/null
    is_column         BOOLEAN DEFAULT 0,
    title             TEXT,                 -- 长文标题，短帖为空
    text              TEXT,                 -- 完整 HTML 内容
    description       TEXT,                 -- 摘要
    created_at        INTEGER NOT NULL,     -- Unix 毫秒时间戳
    edited_at         INTEGER,
    target            TEXT,                 -- 帖子路径
    retweet_status_id INTEGER DEFAULT 0,
    retweet_text      TEXT,                 -- 转发原文
    reply_count       INTEGER DEFAULT 0,
    like_count        INTEGER DEFAULT 0,
    retweet_count     INTEGER DEFAULT 0,
    view_count        INTEGER DEFAULT 0,
    fetched_at        INTEGER NOT NULL
);

CREATE TABLE images (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id       INTEGER NOT NULL REFERENCES posts(id),
    original_url  TEXT NOT NULL,
    local_path    TEXT,           -- 下载失败为空
    seq           INTEGER DEFAULT 0,
    UNIQUE(post_id, original_url)
);

CREATE VIRTUAL TABLE posts_fts USING fts5(
    title, text, description,
    content='posts', content_rowid='id'
);

CREATE TABLE sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

`sync_state` 记录增量断点：`last_sync_time`、`last_post_id`、`total_synced`。

## 爬虫模块

### 文件结构

```
core/xueqiu_scraper.py      # 爬虫核心
scripts/sync_xueqiu.py      # CLI 入口
```

### XueqiuScraper 接口

```python
class XueqiuScraper:
    def __init__(self, db_path, image_dir): ...

    def login_and_sync(self, user_id, headless=False):
        """打开浏览器 → 等待登录 → 全量/增量爬取"""

    def _wait_for_login(self, page) -> dict:
        """轮询 Cookie 中的 u 字段"""

    def _fetch_timeline(self, cookies, user_id, since_id=None):
        """逐页拉取，遇到 since_id 停止"""

    def _fetch_full_article(self, cookies, post_id):
        """获取 type=3 长文完整内容"""

    def _download_images(self, post_id, html_text):
        """下载图片，替换 src 为本地路径"""

    def _save_post(self, post_data):
        """写入 SQLite + 更新 FTS"""

    def _update_sync_state(self, last_post_id, count): ...
```

### CLI

```bash
uv run python scripts/sync_xueqiu.py --user-id 1936609590            # 全量
uv run python scripts/sync_xueqiu.py --user-id 1936609590 --incremental  # 增量
```

## 展示页面

### 路由

```python
GET  /xueqiu                                → 页面
GET  /api/xueqiu/posts                      → 帖子列表（分页+筛选+搜索）
GET  /api/xueqiu/posts/<post_id>            → 单个帖子
GET  /api/xueqiu/images/<post_id>/<filename> → 本地图片
POST /api/xueqiu/sync                       → 触发同步
GET  /api/xueqiu/sync/status                → 同步进度
```

### 列表 API 参数

```
GET /api/xueqiu/posts?page=1&per_page=30&type=all&q=关键词&start_date=2026-01-01&end_date=2026-03-08
```

`type` 可选值：`all`、`original`、`retweet`、`column`

### 双栏布局

```
┌─────────────────────────────────────────────────────────────────┐
│ 雪球跟踪 - 逸修1                    [搜索框🔍]  [同步数据]     │
├──────────────────────┬──────────────────────────────────────────┤
│ [全部|原创|转发|长文] │                                          │
│ [时间范围 ▼]         │                                          │
├──────────────────────┤       选择一篇帖子开始阅读                │
│ 03-08 长文           │                                          │
│ ■ 2026年投资展望     │                                          │
│   关于今年的几个核心… │                                          │
│──────────────────────│                                          │
│ 03-07 原创           │                                          │
│ 铜价创新高背后的逻辑 │                                          │
│   最近铜价突破了…     │                                          │
│──────────────────────│                                          │
│ 03-06 转发           │                                          │
│ 转发@某某: 这篇分析… │                                          │
│──────────────────────│                                          │
│ [加载更多]           │                                          │
└──────────────────────┴──────────────────────────────────────────┘
```

左侧列表（~35%）：筛选栏 + 帖子卡片（日期、类型标签、标题/摘要）+ 加载更多

右侧阅读区（~65%）：标题、发布时间、互动数据、完整 HTML 内容、图片

### 前端状态（Alpine.js）

```javascript
{
  posts: [],           // 当前列表
  selectedId: null,    // 选中帖子 ID
  selectedPost: null,  // 完整内容
  filter: 'all',       // all|original|retweet|column
  query: '',           // 搜索词
  page: 1,
  hasMore: true,
  syncing: false,
  syncProgress: '',
  lastSyncTime: '',
}
```

### 交互细节

- 搜索输入 300ms 防抖
- URL hash 同步选中帖子（`#post-12345`），刷新后恢复
- 图片本地路径优先，`onerror` fallback 到 CDN 原始 URL
- 转发帖用引用块展示原文
- 同步按钮：点击后弹出浏览器窗口，前端轮询进度

### 同步交互流程

1. 点击"同步数据"，页面提示"请在弹出的浏览器窗口中登录雪球"
2. 后端启动 StealthyFetcher（headless=False）
3. 用户登录后爬虫自动开始
4. 前端轮询 `/api/xueqiu/sync/status` 显示进度
5. 完成后刷新列表

## 改动范围

| 文件 | 操作 |
|------|------|
| `core/xueqiu_scraper.py` | 新增 |
| `scripts/sync_xueqiu.py` | 新增 |
| `web/templates/xueqiu.html` | 新增 |
| `web/app.py` | 修改：新增路由 |
| `web/templates/base.html` | 修改：导航栏加"雪球跟踪" |

## 不做的事

- 不爬评论区
- 不支持多用户（只跟踪单个雪球用户）
- 不做移动端适配
