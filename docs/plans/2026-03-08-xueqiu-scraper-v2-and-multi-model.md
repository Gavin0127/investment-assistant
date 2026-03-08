# 雪球爬虫 V2 + LLM 多模型配置 设计文档

日期: 2026-03-08

## 一、雪球爬虫 V2 — fetch() 直接调 API

### 1.1 背景

当前方案（response interception + 滚动加载）存在以下问题：
- 滚动加载 3 秒固定等待不可靠，网络慢时误判为"到底了"
- `_fetch_article_in_browser` 用 `page.goto` 破坏主页上下文，长文后滚动加载全部失效
- 图片展示前后端格式不匹配，功能完全不工作

### 1.2 方案

登录后让浏览器完成 WAF JS Challenge，然后在浏览器内用 `page.evaluate(fetch(...))` 直接调 API，精确控制分页。

```
登录（扫码 / 复用 user_data_dir session）
    ↓
导航到雪球首页，等待 WAF cookie 就绪
    ↓
循环：page.evaluate(fetch("/v4/statuses/user_timeline.json?page=N"))
    ├─ 返回 JSON → 解析、保存帖子
    ├─ 返回 HTML（WAF 拦截）→ 重新导航首页触发 WAF challenge，重试
    └─ 返回空 / 无更多数据 → 结束
    ↓
批量下载图片（不阻塞帖子同步）
```

### 1.3 核心改动

#### 1.3.1 `_browser_fetch_api` — 替代滚动拦截

```python
def _browser_fetch_api(self, page, path: str, params: dict) -> Optional[dict]:
    """在浏览器内用 fetch() 调 API，自动携带 WAF cookie。"""
    url = f"{_BASE_URL}{path}?{urlencode(params)}"
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
        return None  # WAF 拦截，返回 None 触发重试
    return json.loads(resp_text)
```

#### 1.3.2 WAF cookie 就绪检测

登录成功后，导航到首页等待 `networkidle`，再额外等 3 秒让 WAF JS 执行完毕。
如果 fetch 返回 HTML，说明 WAF cookie 没就绪或已过期，重新导航首页触发 challenge。

```python
def _ensure_waf_ready(self, page):
    """确保 WAF cookie 就绪。"""
    page.goto(f"{_BASE_URL}/", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(3000)
```

#### 1.3.3 同步主循环 — 精确分页

```python
def _sync_all(self, page, user_id):
    self._ensure_waf_ready(page)
    pg = 1
    retries = 0
    while pg <= max_pages:
        data = self._browser_fetch_api(page, TIMELINE_API, {"user_id": user_id, "page": pg})
        if data is None:
            # WAF 拦截，重新触发 challenge 后重试
            if retries < 3:
                self._ensure_waf_ready(page)
                retries += 1
                continue
            break
        retries = 0
        posts = self._parse_timeline(data)
        if not posts:
            break
        for post in posts:
            if incremental and self.db.get_post(post["id"]):
                stop = True; continue
            # 长文也用 fetch 获取，不破坏页面上下文
            if self._needs_full_fetch(post):
                full = self._browser_fetch_api(page, SHOW_API, {"id": post["id"]})
                if full: ...
            self.db.save_post(post)
            pending_images.append(...)
        pg += 1
        page.wait_for_timeout(1500)  # 限速
    # 最后批量下载图片
    self._download_images_batch(pending_images)
```

#### 1.3.4 图片展示修复

后端 `api_xueqiu_get_post` 返回 images 时，转换为前端期望的文件名列表：

```python
# web/app.py
images = db.get_images(post_id)
post["images"] = [
    os.path.basename(img["local_path"]) if img.get("local_path") else None
    for img in images
]
```

### 1.4 保留的设计

- `user_data_dir` 持久化浏览器 session，避免重复扫码
- `headless=False` 本地模式弹出浏览器
- 增量同步：遇到已存在帖子标记 stop
- 图片流式下载 + 10MB 限制

---

## 二、LLM 多模型配置

### 2.1 背景

当前 `LLMClient` 只持有一个 model，所有模块共享。`model_pro` / `model_flash` 属性是空壳。
需求：同一个 base_url + key 下配置多个模型，不同任务用不同模型。

### 2.2 方案

#### 2.2.1 config.json 新增字段

```json
{
  "llm_provider": "gemini",
  "llm_model": "gemini-3.1-pro-preview",
  "llm_model_fast": "gemini-3.1-flash",
  "llm_model_reasoning": "gemini-3.1-pro"
}
```

对应环境变量：`LLM_MODEL_FAST`、`LLM_MODEL_REASONING`。
不配置时 fallback 到 `llm_model`（默认模型），完全向后兼容。

#### 2.2.2 LLMClient 改动

```python
class LLMClient:
    def __init__(self, api_key, model=None, provider="gemini",
                 base_url=None, model_fast=None, model_reasoning=None):
        ...
        self.model = model or defaults["model"]
        self._model_fast = model_fast
        self._model_reasoning = model_reasoning

    @property
    def model_fast(self) -> str:
        return self._model_fast or self.model

    @property
    def model_reasoning(self) -> str:
        return self._model_reasoning or self.model

    def chat(self, prompt, model=None):
        """model 参数可覆盖默认模型。"""
        use_model = model or self.model
        response = self.client.chat.completions.create(
            model=use_model, messages=[{"role": "user", "content": prompt}]
        )
        ...

    def chat_with_system(self, system_prompt, user_prompt, model=None):
        use_model = model or self.model
        ...
```

#### 2.2.3 模块与模型对应

| 模块 | 任务 | 模型属性 |
|------|------|---------|
| EnvironmentCollector | 新闻筛选、摘要 | `client.model_fast` |
| PreferenceLearner | 偏好提取 | `client.model_fast` |
| InterviewManager | 苏格拉底式访谈 | `client.model`（默认） |
| ProfitTracker | 利润敏感性分析 | `client.model`（默认） |
| ResearchEngine | 深度研究、影响评估 | `client.model_reasoning` |

各模块不需要改构造函数，只需在 `chat()` 调用时传入对应的 model：

```python
# 例：EnvironmentCollector 中
result = self.client.chat(prompt, model=self.client.model_fast)

# 例：ResearchEngine 中
result = self.client.chat_with_system(system, user, model=self.client.model_reasoning)
```

#### 2.2.4 Web 端初始化

```python
# web/app.py get_client()
client = LLMClient(
    api_key, model=model, provider=provider, base_url=base_url,
    model_fast=storage.get_config("llm_model_fast"),
    model_reasoning=storage.get_config("llm_model_reasoning"),
)
```

#### 2.2.5 向后兼容

- 不配置 `llm_model_fast` / `llm_model_reasoning` → 全部用默认模型
- 现有环境变量和 config.json 字段不变
- 现有测试不受影响（mock client 的 model 属性即可）
