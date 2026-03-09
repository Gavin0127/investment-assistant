# 雪球 V2：多用户爬取 + AI 聊天助手 设计方案

## 目标

将雪球内容爬取功能产品化，支持多用户管理、批次控制、进度展示；新增 AI 聊天助手页面，可基于系统全部数据进行投研分析对话。

## 架构总览

```
用户配置 (xueqiu_users 表)
    ↓
爬取控制 (max_pages) → XueqiuScraper → posts/images (SQLite)
                                              ↓
AI 聊天 ← LLMClient (SSE 流式) ← 系统 Prompt + 帖子上下文 + 历史消息
    ↓
聊天记录 (chat.db)
```

新增/修改文件：

```
core/xueqiu_db.py        — 扩展：xueqiu_users 表 + CRUD
core/xueqiu_scraper.py   — 修改：max_pages 参数
core/chat.py              — 新增：ChatDB + ChatEngine（SSE 流式 + 上下文构建）
web/app.py                — 新增：聊天路由 + 用户管理路由
web/templates/xueqiu.html — 修改：用户选择器 + 爬取控制面板
web/templates/chat.html   — 新增：AI 聊天页面
web/templates/base.html   — 修改：导航栏增加「AI 助手」
```

---

## 一、数据库扩展

### xueqiu_posts.db 新增表

```sql
CREATE TABLE IF NOT EXISTS xueqiu_users (
    user_id INTEGER PRIMARY KEY,
    nickname TEXT NOT NULL,
    avatar_url TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now') * 1000)
);
```

`posts` 表已有 `user_id` 字段，通过外键关联，无需改动。

### 新增 data/chat.db

```sql
CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY,                -- UUID
    title TEXT NOT NULL DEFAULT '新对话',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,                  -- user | assistant | thinking
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
```

`thinking` 角色存储思考过程，前端折叠展示。聊天与雪球数据分库，避免互相影响。

---

## 二、爬取系统改造

### XueqiuScraper 修改

```python
def login_and_sync(self, user_id: int, headless: bool = False, max_pages: int = 5):
    ...
    self._sync_all(page, user_id, max_pages=max_pages)

def _sync_all(self, page, user_id: int, max_pages: int = 5):
    ...
    while not stop and pg <= max_pages:  # 原硬编码 200，改为参数
```

### XueqiuDB 新增方法

```python
def add_user(self, user_id: int, nickname: str) -> None
def remove_user(self, user_id: int) -> None
def list_users(self) -> list[dict]
def get_user(self, user_id: int) -> Optional[dict]
```

### 新增 API 路由

```
POST   /api/xueqiu/users          — 添加用户（user_id + nickname）
DELETE /api/xueqiu/users/<id>      — 删除用户
GET    /api/xueqiu/users           — 用户列表
POST   /api/xueqiu/sync           — 扩展：接收 user_id + max_pages
GET    /api/xueqiu/posts           — 扩展：增加 user_id 筛选参数
```

---

## 三、AI 聊天引擎

### core/chat.py

```python
class ChatDB:
    """聊天记录 SQLite 存储，独立 chat.db"""
    def __init__(self, db_path: str)
    def create_session(self) -> str
    def list_sessions(self) -> list[dict]
    def delete_session(self, session_id: str)
    def add_message(self, session_id: str, role: str, content: str)
    def get_messages(self, session_id: str) -> list[dict]
    def update_session_title(self, session_id: str, title: str)


class ChatEngine:
    def __init__(self, llm_client: LLMClient, xueqiu_db: XueqiuDB, chat_db: ChatDB):
        ...

    def stream_reply(self, session_id: str, user_message: str):
        """生成器，yield SSE 事件"""
        # 1. 保存用户消息
        # 2. 构建上下文（系统 prompt + 雪球帖子 + 历史消息 + 其他系统数据）
        # 3. 调用 LLM 流式 API
        # 4. yield 事件
        # 5. 保存 assistant 消息（thinking + content 分别存储）
```

### 上下文构建策略（不设 token 预算，追求效果最大化）

- 系统 prompt：角色定义（投研分析师）+ 数据源说明 + 输出格式要求
- 雪球帖子：根据用户提问 LIKE 搜索相关帖子，不限条数全部纳入；若指定某用户，拉取该用户全部帖子 description 作为摘要，text 全文按相关性取 top 50
- 历史消息：当前会话全部历史（含 thinking），不截断
- 其他系统数据：若提问涉及个股研究、playbook 等，从 Storage 读取相关 JSON 注入
- 仅当总上下文超过模型窗口限制（Gemini 1M / GPT 128K）时才截断，优先保留最近历史和最相关帖子

### SSE 事件格式

```
data: {"type": "thinking", "content": "正在分析用户的问题..."}
data: {"type": "content", "content": "根据逸修1最近的发言"}
data: {"type": "done", "session_title": "逸修1投研观点总结"}
```

### 会话标题

首轮对话完成后，异步调用 LLM 生成会话标题（不阻塞主流程）。

---

## 四、聊天 API

```
GET    /chat                              — 聊天页面
GET    /api/chat/sessions                 — 会话列表（按 updated_at 倒序）
POST   /api/chat/sessions                 — 创建新会话
DELETE /api/chat/sessions/<id>            — 删除会话及消息
GET    /api/chat/sessions/<id>/messages   — 历史消息
POST   /api/chat/sessions/<id>/messages   — 发送消息（返回 SSE 流）
```

SSE 端点实现：

```python
@app.route('/api/chat/sessions/<session_id>/messages', methods=['POST'])
def api_chat_send(session_id):
    user_msg = request.json["content"]
    engine = _get_chat_engine()

    def generate():
        for event in engine.stream_reply(session_id, user_msg):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )
```

---

## 五、雪球页面 UI 改造

顶部增加用户选择器和爬取控制：

```
┌─────────────────────────────────────────────────────────────┐
│ 雪球跟踪                                                     │
│ [▼ 逸修1 (1936609590)] [+]    搜索[________]  [同步数据 ▼]  │
│                                                             │
│                               同步设置弹窗：                  │
│                               ┌──────────────┐              │
│                               │ 爬取页数: [5] │              │
│                               │ [开始同步]    │              │
│                               └──────────────┘              │
├──────────┬──────────────────────────────────────────────────┤
│ 帖子列表  │  帖子详情                                        │
│ (按选中   │                                                  │
│  用户筛选) │                                                  │
└──────────┴──────────────────────────────────────────────────┘
```

- 用户选择器：下拉切换，帖子列表按 user_id 筛选刷新
- 「+」按钮：弹窗输入 user_id + 昵称
- 同步按钮：下拉展开设置面板，可调页数（默认 5）
- 进度条：同步中顶部显示「第 3/5 页，已保存 47 条」

---

## 六、AI 聊天页面 UI

导航栏新增「AI 助手」入口，经典三栏 chatbot 布局：

```
┌──────────┬──────────────────────────────────────────┐
│ 会话列表  │  聊天区域                                  │
│          │                                          │
│ [+ 新对话] │  ┌─ 思考过程（折叠）──────────────┐       │
│          │  │ ▶ 思考中... (点击展开)          │       │
│ · 逸修1观点│  └──────────────────────────────┘       │
│ · 持仓分析 │                                         │
│ · ...    │  根据逸修1最近30天的发言，他的核心       │
│          │  观点集中在以下几个方面...                │
│          │                                          │
│          ├──────────────────────────────────────────┤
│          │  [输入框...                    ] [发送]   │
└──────────┴──────────────────────────────────────────┘
```

- 左侧会话列表：按 updated_at 倒序，点击切换，可删除
- 输入框：Enter 发送，Shift+Enter 换行
- 思考过程：流式输出时显示「思考中...」动画，完成后折叠，点击展开
- 正文：Markdown 渲染（marked.js + DOMPurify）
- 技术栈：Alpine.js + Tailwind + marked.js + DOMPurify，SSE 用 fetch + ReadableStream

---

## 七、错误处理

- 爬取失败：单页失败不中断同步，记录日志跳过。WAF 保留 3 次重试
- 用户添加：校验 user_id 为正整数、昵称非空，重复返回 409
- SSE 中断：前端检测断开显示「生成中断」，可点「重新生成」
- LLM 调用失败：yield `{"type": "error", "content": "..."}` 事件

---

## 八、测试

```
tests/test_xueqiu_db.py      — 扩展：用户 CRUD
tests/test_xueqiu_scraper.py — 扩展：max_pages 参数
tests/test_chat_db.py         — 新增：会话/消息 CRUD
tests/test_chat_engine.py     — 新增：上下文构建、流式输出（mock LLM）
tests/test_chat_api.py        — 新增：API 路由（SSE 响应格式）
```

所有测试用 mock 隔离外部依赖，可离线运行。
