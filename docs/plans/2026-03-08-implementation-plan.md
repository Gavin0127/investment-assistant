# 雪球爬虫 V2 + LLM 多模型 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将雪球爬虫从滚动拦截方案改为 fetch() 直接调 API，修复图片展示，并为 LLM 客户端增加多模型支持。

**Architecture:** 爬虫改用 `page.evaluate(fetch(...))` 在浏览器内直接调 API，精确控制分页，WAF 拦截时自动重试。LLM 客户端在 `chat()` 方法增加可选 `model` 参数，config 新增 `llm_model_fast` / `llm_model_reasoning`。

**Tech Stack:** Python 3.14, scrapling (Playwright), SQLite, Flask, OpenAI SDK

---

### Task 1: LLMClient 多模型支持

**Files:**
- Modify: `core/openai_client.py:42-90`
- Modify: `core/storage.py:46-48`
- Test: `tests/test_llm_client.py`

**Step 1: 写失败测试 — model_fast / model_reasoning 属性**

在 `tests/test_llm_client.py` 的 `TestLLMClientInit` 类末尾添加：

```python
def test_model_fast_defaults_to_model(self):
    client = LLMClient(api_key="test-key", provider="openai")
    assert client.model_fast == client.model

def test_model_reasoning_defaults_to_model(self):
    client = LLMClient(api_key="test-key", provider="openai")
    assert client.model_reasoning == client.model

def test_model_fast_custom(self):
    client = LLMClient(api_key="test-key", provider="openai", model_fast="gpt-4o-mini")
    assert client.model_fast == "gpt-4o-mini"
    assert client.model == "gpt-5.4"  # 默认不变

def test_model_reasoning_custom(self):
    client = LLMClient(api_key="test-key", provider="openai", model_reasoning="o1-pro")
    assert client.model_reasoning == "o1-pro"
    assert client.model == "gpt-5.4"
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_llm_client.py::TestLLMClientInit::test_model_fast_custom -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'model_fast'`

**Step 3: 实现 LLMClient 多模型**

修改 `core/openai_client.py`：

`__init__` 增加 `model_fast` 和 `model_reasoning` 参数：

```python
def __init__(
    self,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    provider: str = "gemini",
    base_url: Optional[str] = None,
    model_fast: Optional[str] = None,
    model_reasoning: Optional[str] = None,
):
    ...（现有代码不变）
    self._model_fast = model_fast
    self._model_reasoning = model_reasoning
```

替换 `model_pro` / `model_flash` 属性：

```python
@property
def model_fast(self) -> str:
    return self._model_fast or self.model

@property
def model_reasoning(self) -> str:
    return self._model_reasoning or self.model
```

删除旧的 `model_pro` 和 `model_flash` 属性。

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_llm_client.py::TestLLMClientInit -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add core/openai_client.py tests/test_llm_client.py
git commit -m "feat(llm): 增加 model_fast / model_reasoning 多模型支持"
```

---

### Task 2: chat() / chat_with_system() 增加 model 参数

**Files:**
- Modify: `core/openai_client.py:72-113`
- Test: `tests/test_llm_client.py`

**Step 1: 写失败测试 — chat 使用自定义 model**

在 `tests/test_llm_client.py` 的 `TestChat` 类末尾添加：

```python
def test_chat_with_custom_model(self, mock_openai_client):
    result = mock_openai_client.chat("hello", model="custom-model")
    assert result == "mock response"
    # 验证传给 SDK 的 model 参数
    call_kwargs = mock_openai_client.client.chat.completions.create.call_args
    assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") == "custom-model"

def test_chat_with_system_custom_model(self, mock_openai_client):
    result = mock_openai_client.chat_with_system("sys", "hello", model="custom-model")
    assert result == "mock response"
    call_kwargs = mock_openai_client.client.chat.completions.create.call_args
    assert call_kwargs.kwargs.get("model") or call_kwargs[1].get("model") == "custom-model"
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_llm_client.py::TestChat::test_chat_with_custom_model -v`
Expected: FAIL — `chat() got an unexpected keyword argument 'model'`

**Step 3: 修改 chat() 和 chat_with_system()**

`core/openai_client.py` — `chat` 方法（约第 72 行）：

```python
def chat(self, prompt: str, history: Optional[List[Dict]] = None,
         model: Optional[str] = None) -> str:
    """普通对话。model 可覆盖默认模型。"""
    use_model = model or self.model
    ...（messages 构建不变）
    resp = self.client.chat.completions.create(
        model=use_model,
        messages=messages,
        timeout=120,
    )
    return resp.choices[0].message.content or ""
```

`chat_with_system` 方法（约第 92 行）：

```python
def chat_with_system(self, system_prompt: str, user_message: str,
                     history: Optional[List[Dict]] = None,
                     model: Optional[str] = None) -> str:
    """带系统提示的对话。model 可覆盖默认模型。"""
    use_model = model or self.model
    ...（messages 构建不变）
    resp = self.client.chat.completions.create(
        model=use_model,
        messages=messages,
        timeout=120,
    )
    return resp.choices[0].message.content or ""
```

**Step 4: 运行全部 LLM 测试确认通过**

Run: `uv run python -m pytest tests/test_llm_client.py -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add core/openai_client.py tests/test_llm_client.py
git commit -m "feat(llm): chat() 支持 model 参数覆盖默认模型"
```

---

### Task 3: Storage 和 Web 端多模型配置

**Files:**
- Modify: `core/storage.py:46-48`
- Modify: `web/app.py:82-96`

**Step 1: Storage 增加读取方法**

在 `core/storage.py` 的 `get_llm_model` 方法后面添加：

```python
def get_llm_model_fast(self) -> Optional[str]:
    return os.environ.get("LLM_MODEL_FAST") or self.get_config().get("llm_model_fast")

def get_llm_model_reasoning(self) -> Optional[str]:
    return os.environ.get("LLM_MODEL_REASONING") or self.get_config().get("llm_model_reasoning")
```

**Step 2: Web 端 get_client() 传入多模型**

修改 `web/app.py` 的 `get_client()` 函数，在创建 `LLMClient` 时传入：

```python
client = LLMClient(
    api_key, model=model, provider=provider, base_url=base_url,
    model_fast=storage.get_llm_model_fast(),
    model_reasoning=storage.get_llm_model_reasoning(),
)
```

**Step 3: 运行现有测试确认不破坏**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: ALL PASS（向后兼容，不配置新字段时行为不变）

**Step 4: 提交**

```bash
git add core/storage.py web/app.py
git commit -m "feat(llm): Storage 和 Web 端支持多模型配置"
```

---

### Task 4: 雪球爬虫 — _browser_fetch_api 和 _ensure_waf_ready

**Files:**
- Modify: `core/xueqiu_scraper.py`
- Test: `tests/test_xueqiu_scraper.py`

**Step 1: 写测试 — _browser_fetch_api 解析 JSON**

在 `tests/test_xueqiu_scraper.py` 中添加：

```python
class TestBrowserFetchApi:
    def test_parse_json_response(self, tmp_path):
        """fetch 返回 JSON 时正确解析。"""
        scraper = XueqiuScraper(str(tmp_path / "xq.db"), str(tmp_path / "imgs"))
        mock_page = MagicMock()
        mock_page.evaluate.return_value = '{"statuses": [{"id": 1}]}'
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result == {"statuses": [{"id": 1}]}

    def test_detect_waf_html(self, tmp_path):
        """fetch 返回 HTML 时识别为 WAF 拦截。"""
        scraper = XueqiuScraper(str(tmp_path / "xq.db"), str(tmp_path / "imgs"))
        mock_page = MagicMock()
        mock_page.evaluate.return_value = '<textarea id="renderData">...</textarea><!doctype html>'
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result is None

    def test_detect_empty_response(self, tmp_path):
        """fetch 返回空字符串时识别为失败。"""
        scraper = XueqiuScraper(str(tmp_path / "xq.db"), str(tmp_path / "imgs"))
        mock_page = MagicMock()
        mock_page.evaluate.return_value = ''
        result = scraper._browser_fetch_api(mock_page, "/test", {"page": 1})
        assert result is None
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestBrowserFetchApi -v`
Expected: FAIL — `XueqiuScraper has no attribute '_browser_fetch_api'`

**Step 3: 实现 _browser_fetch_api 和 _ensure_waf_ready**

在 `core/xueqiu_scraper.py` 中添加两个方法：

```python
def _ensure_waf_ready(self, page):
    """导航到雪球首页，等待 WAF JS Challenge 完成。"""
    self.sync_progress = "正在通过 WAF 验证..."
    page.goto(f"{_BASE_URL}/", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)

def _browser_fetch_api(self, page, path: str, params: dict) -> Optional[dict]:
    """在浏览器内用 fetch() 调 API，自动携带 WAF cookie。"""
    from urllib.parse import urlencode
    url = f"{_BASE_URL}{path}?{urlencode(params)}"
    try:
        resp_text = page.evaluate("""
            async (url) => {
                const resp = await fetch(url, {
                    credentials: 'include',
                    headers: { 'Accept': 'application/json' }
                });
                return await resp.text();
            }
        """, url)
        if not resp_text or resp_text.lstrip().startswith('<'):
            logger.warning("WAF blocked fetch for %s", path)
            return None
        return json.loads(resp_text)
    except Exception as e:
        logger.error("Browser fetch failed: %s %s — %s", path, params, e)
        return None
```

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py::TestBrowserFetchApi -v`
Expected: ALL PASS

**Step 5: 提交**

```bash
git add core/xueqiu_scraper.py tests/test_xueqiu_scraper.py
git commit -m "feat(xueqiu): 增加 _browser_fetch_api 和 _ensure_waf_ready"
```

---

### Task 5: 雪球爬虫 — 重写同步主循环

**Files:**
- Modify: `core/xueqiu_scraper.py:44-86` (login_and_sync)
- Modify: `core/xueqiu_scraper.py:88-185` (替换 _sync_via_interception)

**Step 1: 重写 login_and_sync 调用新方法**

将 `login_then_sync` 内部的 `self._sync_via_interception(page, user_id)` 改为 `self._sync_all(page, user_id)`。

**Step 2: 用 _sync_all 替换 _sync_via_interception**

删除 `_sync_via_interception` 和 `_fetch_article_in_browser`，新增 `_sync_all`：

```python
def _sync_all(self, page, user_id: int):
    """用 fetch() 精确分页同步所有帖子。"""
    self.sync_status = "syncing"
    self._ensure_waf_ready(page)

    last_id = self.db.get_latest_post_id()
    incremental = last_id is not None
    total_saved = 0
    stop = False
    pending_images: list = []  # [(post_id, url, seq), ...]
    max_pages = 200
    waf_retries = 0

    pg = 1
    while not stop and pg <= max_pages:
        self.sync_progress = f"正在拉取第 {pg} 页..."
        logger.info("Fetching timeline page %d", pg)

        data = self._browser_fetch_api(
            page, _TIMELINE_API, {"user_id": user_id, "page": pg}
        )
        if data is None:
            if waf_retries < 3:
                logger.warning("WAF retry %d/3", waf_retries + 1)
                self._ensure_waf_ready(page)
                waf_retries += 1
                continue
            logger.error("WAF retries exhausted, stopping")
            break
        waf_retries = 0

        posts = self._parse_timeline(data)
        if not posts:
            break

        for post in posts:
            if incremental and self.db.get_post(post["id"]):
                stop = True
                continue

            if self._needs_full_fetch(post):
                full = self._browser_fetch_api(
                    page, _SHOW_API, {"id": post["id"]}
                )
                if full:
                    post["text"] = full.get("text", post.get("text", ""))
                    post["title"] = full.get("title", post.get("title"))

            img_urls = self._extract_image_urls(post.get("text"))
            self.db.save_post(post)
            for seq, url in enumerate(img_urls):
                pending_images.append((post["id"], url, seq))

            total_saved += 1
            self.sync_count = total_saved
            self.sync_progress = f"已保存 {total_saved} 条帖子"

        pg += 1
        page.wait_for_timeout(1500)

    # 批量下载图片
    if pending_images:
        self.sync_progress = f"正在下载 {len(pending_images)} 张图片..."
        for i, (post_id, url, seq) in enumerate(pending_images):
            local = self._download_image(post_id, url, seq)
            self.db.save_image(post_id, url, local, seq)
            if (i + 1) % 10 == 0:
                self.sync_progress = f"图片 {i+1}/{len(pending_images)}"

    self.db.set_sync_state("last_sync_time", str(int(time.time())))
    self.db.set_sync_state("total_synced", str(total_saved))
    self.sync_status = "done"
    self.sync_progress = f"同步完成，共 {total_saved} 条"
    logger.info("Sync complete: %d posts, %d images", total_saved, len(pending_images))
```

**Step 3: 删除废弃方法**

删除以下方法（不再使用）：
- `_sync_via_interception`
- `_fetch_article_in_browser`
- `_api_get`
- `_fetch_full_article`

**Step 4: 运行全部雪球测试**

Run: `uv run python -m pytest tests/test_xueqiu_scraper.py tests/test_xueqiu_db.py tests/test_xueqiu_api.py -v --tb=short`
Expected: ALL PASS（如果有测试引用了删除的方法，需要同步更新）

**Step 5: 提交**

```bash
git add core/xueqiu_scraper.py tests/
git commit -m "refactor(xueqiu): 用 fetch() 精确分页替代滚动拦截方案"
```

---

### Task 6: 修复图片展示 — 后端返回格式

**Files:**
- Modify: `web/app.py` (api_xueqiu_get_post 路由)
- Test: `tests/test_xueqiu_api.py`

**Step 1: 写测试 — images 返回文件名列表**

在 `tests/test_xueqiu_api.py` 中添加（或修改已有测试）：

```python
def test_get_post_images_format(client, xueqiu_db):
    """images 应返回文件名字符串列表，而非 dict 列表。"""
    xueqiu_db.save_post({"id": 100, "user_id": 1, "type": "2",
                          "text": "test", "created_at": 1000})
    xueqiu_db.save_image(100, "https://example.com/a.jpg",
                          "/path/to/images/100/000.jpg", 0)
    resp = client.get("/api/xueqiu/posts/100")
    data = resp.get_json()
    assert isinstance(data["images"], list)
    assert data["images"][0] == "000.jpg"
```

**Step 2: 修改后端路由**

在 `web/app.py` 的 `api_xueqiu_get_post` 路由中，修改 images 返回格式：

```python
images = db.get_images(post_id)
post["images"] = [
    os.path.basename(img["local_path"]) if img.get("local_path") else None
    for img in images
]
# 过滤掉 None（下载失败的图片）
post["images"] = [f for f in post["images"] if f]
```

**Step 3: 运行测试**

Run: `uv run python -m pytest tests/test_xueqiu_api.py -v --tb=short`
Expected: ALL PASS

**Step 4: 提交**

```bash
git add web/app.py tests/test_xueqiu_api.py
git commit -m "fix(xueqiu): 修复图片展示后端返回格式"
```

---

### Task 7: 全量测试 + 清理

**Files:**
- All modified files

**Step 1: 运行全量测试**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

**Step 2: 检查是否有未使用的 import**

检查 `core/xueqiu_scraper.py` 是否还需要 `import requests`（图片下载仍然用 requests，所以保留）。

**Step 3: 最终提交**

如果有任何清理改动：

```bash
git add -A
git commit -m "chore: 清理废弃代码和未使用的 import"
```
