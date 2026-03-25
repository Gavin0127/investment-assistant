# 阿里云 WAF 反爬技术调研：雪球网站爬取方案

> 调研日期：2026-03-08

## 一、阿里云 WAF 的 acw_sc__v2 / acw_sc__v3 Cookie Challenge 机制

### 1.1 整体流程

雪球使用的是阿里云 WAF 的 JS Challenge 机制，核心是 `acw_sc__v2` cookie。流程如下：

```
浏览器首次请求 xueqiu.com/today
    ↓
WAF 返回 HTTP 200，但 body 不是正常页面
而是一段混淆的 JS 代码（约 2KB）
同时 Set-Cookie: acw_tc=... (WAF 会话标识)
    ↓
JS 代码在浏览器中执行：
  1. 定义 arg1 = '6A6BE0CAF2D2305297951C9A2ADBC2E8D21D48FD' (每次不同)
  2. 对 arg1 执行 unsbox() 置换 + hexXor() 异或运算
  3. 计算结果写入 document.cookie: acw_sc__v2=<result>
  4. 调用 reload() 刷新页面
    ↓
浏览器第二次请求，携带 acw_sc__v2 cookie
    ↓
WAF 验证通过，返回正常页面内容
同时可能 Set-Cookie 其他值（xq_a_token 等）
```

### 1.2 acw_sc__v2 算法细节

核心算法已被逆向，本质是两步操作：

```javascript
// 1. unsbox(): 字符位置置换（固定置换表）
String.prototype.unsbox = function() {
    var box = [0xf, 0x23, 0x1d, 0x18, 0x21, 0x10, 0x1, 0x26,
               0xa, 0x9, 0x13, 0x1f, 0x28, 0x1b, 0x16, 0x17,
               0x19, 0xd, 0x6, 0xb, 0x27, 0x12, 0x14, 0x8,
               0xe, 0x15, 0x20, 0x1a, 0x2, 0x1e, 0x7, 0x4,
               0x11, 0x5, 0x3, 0x1c, 0x22, 0x25, 0xc, 0x24];
    // 按 box 表重新排列字符
    ...
};

// 2. hexXor(): 与固定密钥做十六进制异或
//    密钥固定为 '3000176000856006061501533003690027800375'
var key = '3000176000856006061501533003690027800375';
arg2 = arg1.unsbox().hexXor(key);
// arg2 就是 acw_sc__v2 的值
```

关键点：
- `arg1` 每次请求都不同（服务端生成），但算法和密钥是固定的
- 置换表 `box` 和异或密钥 `key` 长期不变（至少从 2021 年到现在）
- GitHub 上有现成的生成器：[WangYihang/acw-sc-v2.js](https://github.com/WangYihang/acw-sc-v2.js)

### 1.3 acw_sc__v3 情况

`acw_sc__v3` 是更新版本的 challenge，目前在雪球上尚未观察到强制使用。部分阿里云 WAF 客户已升级到 v3，v3 的 JS 混淆更深，加入了环境检测（检查 `window`、`document`、`navigator` 等对象的完整性），但核心思路类似。

### 1.4 反 debugger 机制

雪球的 WAF JS 代码中内置了反调试：
- 通过拼接字符串构造 `debugger` 语句，阻止开发者工具调试
- 使用 `setInterval` 持续触发 debugger
- 绕过方式：在 DevTools 中右键 → "Never pause here"，或通过 hook `Function.prototype.constructor` 跳过

## 二、阿里云 WAF 对 XHR/fetch 请求的检测维度

### 2.1 Cookie 验证（最关键）

WAF 检查以下 cookie 是否存在且有效：

| Cookie | 来源 | 作用 |
|--------|------|------|
| `acw_tc` | WAF Set-Cookie | WAF 会话标识，首次访问即下发 |
| `acw_sc__v2` | JS Challenge 计算 | 证明客户端能执行 JS |
| `xq_a_token` | 雪球登录/首页 | 雪球 API 认证 token |
| `u` | 雪球登录 | 用户 ID |

缺少 `acw_sc__v2` → 返回 JS challenge 页面（不是 403）
缺少 `xq_a_token` → API 返回 `{"error_code": "400016", "error_description": "未登录"}`

### 2.2 Sec-Fetch-* Headers

现代浏览器自动附加的 Fetch Metadata headers，WAF 会参考但通常不作为硬性拦截条件：

```
Sec-Fetch-Site: same-origin    ← 同源请求（页面内 XHR/fetch）
Sec-Fetch-Mode: cors           ← XHR/fetch 请求
Sec-Fetch-Dest: empty          ← 非导航请求
Sec-Ch-Ua: "Chromium";v="131"  ← 浏览器品牌标识
Sec-Ch-Ua-Platform: "macOS"    ← 操作系统
```

在真实浏览器内发起的 `fetch()` 请求，这些 headers 由浏览器自动设置，无法伪造也无需伪造。纯 HTTP 客户端（requests/httpx）不会发送这些 headers，但目前雪球 WAF 对此的检测不严格——只要 cookie 正确，缺少 Sec-Fetch-* 也能通过。

### 2.3 Referer 检查

雪球 API 要求 `Referer` 以 `https://xueqiu.com` 开头。缺少 Referer 可能导致 403。

### 2.4 TLS 指纹

阿里云 WAF 具备 TLS 指纹检测能力（JA3/JA4），但雪球目前未启用严格的 TLS 指纹校验。Python `requests` 库的默认 TLS 指纹与浏览器差异较大，但目前仍可通过。

### 2.5 请求频率

WAF 有频率限制，过快请求会触发验证码或临时封禁 IP。建议：
- 每次请求间隔 2-5 秒
- 单次 session 不超过 200 次请求
- 使用随机延迟

## 三、GitHub 上成功的雪球爬虫项目分析

### 3.1 pysnowball（最流行，2.3k stars）

地址：https://github.com/uname-yang/pysnowball

方案：**纯 HTTP + 手动 token**
```python
import pysnowball as ball
ball.set_token("xq_a_token=662745a236*****;u=909119****")
ball.quotec('SZ002027')
```

工作原理：
- 用户手动从浏览器 DevTools 复制 `xq_a_token` 和 `u` cookie
- 直接用 requests 调用雪球 API
- 不处理 `acw_sc__v2`（因为 token 本身已经包含了认证信息）
- token 有效期约 1 个月

局限：
- 需要手动获取 token，无法自动化
- token 过期后需要重新获取
- 不适合需要登录态的操作（如获取用户 timeline 全量数据）

### 3.2 xueqiu-api（Node.js）

地址：https://github.com/bellchet58/xueqiu-api

方案：类似 pysnowball，手动设置 cookie。

### 3.3 K哥爬虫的逆向方案

方案：**JS 逆向 + requests**
```python
# 1. 首次请求获取 JS challenge 页面
response = requests.get(url=index_url, headers=headers)
# 2. 从响应中提取 arg1
arg1 = re.findall("arg1='(.*?)'", response.text)[0]
# 3. 用逆向的 JS 计算 acw_sc__v2
acw_sc__v2 = execjs.compile(js_code).call('getAcwScV2', arg1)
# 4. 带上 acw_sc__v2 再次请求
response2 = requests.get(url, cookies={"acw_sc__v2": acw_sc__v2, ...})
```

优点：纯 HTTP，速度快
缺点：依赖 `execjs`（需要 Node.js 环境），算法可能更新

### 3.4 共同特点

所有成功的雪球爬虫都遵循一个模式：
1. 先获取有效的 cookie（手动或自动）
2. 用 cookie 直接调用 API
3. 不依赖浏览器滚动加载

## 四、在 Playwright 浏览器内用 fetch() 发请求确保 WAF Cookie 就绪

### 4.1 核心思路

在浏览器内用 `page.evaluate()` 执行 `fetch()` 是最可靠的方案，因为：
- 浏览器已经完成了 JS Challenge，`acw_sc__v2` cookie 已就绪
- `fetch()` 自动携带同源 cookie（包括 `acw_sc__v2`、`xq_a_token`、`u` 等）
- 浏览器自动设置正确的 `Sec-Fetch-*` headers
- TLS 指纹是真实浏览器的指纹
- 对 WAF 来说，这和用户在页面上点击触发的请求完全一样

### 4.2 确保 Cookie 就绪的方法

```python
async def ensure_waf_ready(page):
    """确保 WAF cookie 已就绪"""
    # 方法 1：等待 network idle（推荐）
    await page.goto('https://xueqiu.com/', wait_until='networkidle')

    # 方法 2：显式检查 cookie
    cookies = await page.context.cookies()
    cookie_names = {c['name'] for c in cookies}
    required = {'acw_sc__v2', 'xq_a_token'}
    if not required.issubset(cookie_names):
        # cookie 未就绪，等待
        await page.wait_for_timeout(3000)
        # 重新检查...

    # 方法 3：尝试一次 API 调用验证
    result = await page.evaluate('''
        async () => {
            const resp = await fetch('/v4/statuses/user_timeline.json?page=1&user_id=1936609590');
            return { status: resp.status, ok: resp.ok };
        }
    ''')
    if not result['ok']:
        raise Exception('WAF cookie not ready')
```

### 4.3 在浏览器内发 API 请求

```python
async def fetch_timeline_in_browser(page, user_id, page_num):
    """在浏览器内用 fetch() 调用 API"""
    data = await page.evaluate(f'''
        async () => {{
            const resp = await fetch(
                '/v4/statuses/user_timeline.json?page={page_num}&user_id={user_id}',
                {{
                    method: 'GET',
                    credentials: 'include',  // 自动携带 cookie
                    headers: {{
                        'Accept': 'application/json',
                        'X-Requested-With': 'XMLHttpRequest'
                    }}
                }}
            );
            return await resp.json();
        }}
    ''')
    return data
```

### 4.4 与当前方案（response interception + 滚动）的对比

| 维度 | 当前方案（滚动 + 拦截） | 推荐方案（浏览器内 fetch） |
|------|------------------------|--------------------------|
| 可靠性 | 低：滚动可能不触发加载 | 高：直接调用 API |
| 速度 | 慢：需要等待渲染和滚动 | 快：纯 API 调用 |
| 分页控制 | 差：依赖页面行为 | 好：精确控制 page 参数 |
| WAF 兼容 | 好：浏览器原生行为 | 好：同源 fetch 等同原生 |
| 错误处理 | 难：拦截可能丢失响应 | 易：直接获取返回值 |
| 长文获取 | 需要导航到详情页 | 直接 fetch show API |

## 五、推荐方案：浏览器内 fetch() 替代滚动加载

### 5.1 方案概述

```
StealthyFetcher 打开浏览器 → 用户登录（或复用 user_data_dir 中的 session）
    ↓
等待 WAF JS Challenge 完成（检查 acw_sc__v2 cookie）
    ↓
在浏览器内用 page.evaluate + fetch() 逐页调用 timeline API
    ↓
每页数据直接返回 JSON，无需拦截或滚动
    ↓
type=3 长文 → 在浏览器内 fetch show API 获取全文
    ↓
图片下载用 requests（CDN 图片不需要 WAF cookie）
```

### 5.2 关键代码改造

```python
def _sync_via_fetch(self, page, user_id: int):
    """用浏览器内 fetch() 替代滚动拦截"""
    self.sync_status = "syncing"
    last_id = self.db.get_latest_post_id()
    incremental = last_id is not None
    total_saved = 0
    stop = False

    # 先确认 WAF cookie 就绪
    self._wait_for_waf_cookie(page)

    pg = 1
    max_pages = 200  # 安全上限

    while not stop and pg <= max_pages:
        self.sync_progress = f"正在获取第 {pg} 页..."
        logger.info("Fetching page %d via in-browser fetch", pg)

        # 在浏览器内发起 fetch 请求
        data = page.evaluate(
            '''(params) => {
                return fetch(
                    `/v4/statuses/user_timeline.json?page=${params.page}&user_id=${params.userId}`,
                    { credentials: 'include' }
                ).then(r => r.json());
            }''',
            {"page": pg, "userId": user_id}
        )

        if not data or not data.get("statuses"):
            logger.info("No more statuses at page %d", pg)
            break

        posts = self._parse_timeline(data)
        for post in posts:
            if incremental and self.db.get_post(post["id"]):
                stop = True
                continue

            if self._needs_full_fetch(post):
                full = self._fetch_article_via_fetch(page, post["id"])
                if full:
                    post["text"] = full.get("text", post.get("text", ""))
                    post["title"] = full.get("title", post.get("title"))

            img_urls = self._extract_image_urls(post.get("text"))
            self.db.save_post(post)
            for seq, url in enumerate(img_urls):
                local = self._download_image(post["id"], url, seq)
                self.db.save_image(post["id"], url, local, seq)
            total_saved += 1
            self.sync_count = total_saved
            self.sync_progress = f"已保存 {total_saved} 条"

        pg += 1
        # 随机延迟 2-4 秒，避免触发频率限制
        delay = 2000 + int(random.random() * 2000)
        page.wait_for_timeout(delay)

def _wait_for_waf_cookie(self, page, max_retries=10):
    """等待 WAF JS Challenge 完成"""
    for i in range(max_retries):
        cookies = page.context.cookies()
        cookie_names = {c["name"] for c in cookies}
        if "acw_sc__v2" in cookie_names or "xq_a_token" in cookie_names:
            logger.info("WAF cookie ready: %s", cookie_names)
            return
        logger.info("Waiting for WAF cookie... attempt %d", i + 1)
        page.wait_for_timeout(2000)
    logger.warning("WAF cookie not detected, proceeding anyway")

def _fetch_article_via_fetch(self, page, post_id: int) -> Optional[dict]:
    """在浏览器内 fetch 长文详情"""
    try:
        data = page.evaluate(
            '''(postId) => {
                return fetch(
                    `/statuses/show.json?id=${postId}`,
                    { credentials: 'include' }
                ).then(r => r.json());
            }''',
            post_id
        )
        return data
    except Exception as e:
        logger.warning("Failed to fetch article %d: %s", post_id, e)
        return None
```

### 5.3 为什么这个方案最稳定

1. **WAF 完全透明**：浏览器已完成 JS Challenge，`fetch()` 自动携带所有 cookie
2. **无需逆向**：不需要理解 `acw_sc__v2` 的算法，浏览器自动处理
3. **精确分页**：直接控制 `page` 参数，不依赖滚动触发
4. **错误可控**：`fetch()` 返回值直接可用，不存在拦截丢失的问题
5. **速度可接受**：每页 2-4 秒延迟，3800 条约 182 页，总计约 10-15 分钟
6. **长期稳定**：只要雪球 API 不变，方案就不会失效

## 六、scrapling 的 real_chrome 参数分析

### 6.1 real_chrome 做了什么

`real_chrome=True` 时，scrapling 的 StealthyFetcher 会：
1. 检测系统中安装的 Google Chrome 浏览器
2. 通过 CDP（Chrome DevTools Protocol）连接到真实 Chrome 实例
3. 使用 rebrowser-patches 修补 CDP 运行时指纹泄漏

与默认的 Chromium 相比：
- TLS 指纹是真实 Chrome 的（JA3 完全匹配）
- `navigator.webdriver` 不会被检测到
- Chrome 的自动更新机制保持浏览器版本最新
- 所有浏览器 API 行为与真实用户一致

### 6.2 对绕过阿里云 WAF 的帮助

| 检测维度 | Chromium (默认) | real_chrome=True |
|----------|----------------|------------------|
| TLS 指纹 | Chromium 特征，可能被标记 | 真实 Chrome，完全正常 |
| CDP 泄漏 | Playwright 有已知泄漏 | rebrowser-patches 修补 |
| User-Agent | 需要手动设置 | 自动使用 Chrome 真实 UA |
| WebDriver 标志 | 需要额外处理 | 自动隐藏 |
| 浏览器指纹 | 部分 API 行为异常 | 完全正常 |

### 6.3 建议

对于雪球场景，`real_chrome=True` 有帮助但不是必须的：
- 雪球的 WAF 主要依赖 cookie challenge，不做深度浏览器指纹检测
- StealthyFetcher 默认的 patchright（Playwright 的反检测 fork）已经足够
- 如果遇到问题，再启用 `real_chrome=True` 作为升级方案

推荐配置：
```python
StealthyFetcher.fetch(
    f"{_BASE_URL}/",
    headless=headless,
    real_chrome=False,       # 默认 patchright 即可
    block_webrtc=True,       # 防止 IP 泄漏
    hide_canvas=True,        # 防止 canvas 指纹
    network_idle=True,       # 等待 JS Challenge 完成
    timeout=60000,
    user_data_dir=self._user_data_dir,
    page_action=login_then_sync,
)
```

## 七、综合推荐方案

### 7.1 最终架构

```
┌─────────────────────────────────────────────────────┐
│                  XueqiuScraper                       │
├─────────────────────────────────────────────────────┤
│                                                      │
│  Phase 1: 浏览器启动 + 登录                          │
│  ┌─────────────────────────────────────────────┐    │
│  │ StealthyFetcher (patchright)                 │    │
│  │ - user_data_dir 持久化 session               │    │
│  │ - 首次需要用户扫码登录                        │    │
│  │ - 后续复用 cookie（约 1 个月有效）            │    │
│  │ - WAF JS Challenge 自动完成                   │    │
│  └─────────────────────────────────────────────┘    │
│                       ↓                              │
│  Phase 2: 数据获取（浏览器内 fetch）                 │
│  ┌─────────────────────────────────────────────┐    │
│  │ page.evaluate + fetch()                      │    │
│  │ - 逐页调用 timeline API                      │    │
│  │ - 长文调用 show API                          │    │
│  │ - 自动携带所有 cookie                        │    │
│  │ - 随机延迟 2-4 秒/页                         │    │
│  └─────────────────────────────────────────────┘    │
│                       ↓                              │
│  Phase 3: 图片下载（纯 HTTP）                        │
│  ┌─────────────────────────────────────────────┐    │
│  │ requests.get()                               │    │
│  │ - CDN 图片不需要 WAF cookie                  │    │
│  │ - 流式下载 + 10MB 限制                       │    │
│  └─────────────────────────────────────────────┘    │
│                       ↓                              │
│  Phase 4: 存储                                       │
│  ┌─────────────────────────────────────────────┐    │
│  │ SQLite + FTS5                                │    │
│  └─────────────────────────────────────────────┘    │
│                                                      │
└─────────────────────────────────────────────────────┘
```

### 7.2 与当前实现的差异

当前实现（`_sync_via_interception`）的问题：
1. 依赖 `page.on("response")` 拦截 + 滚动触发，不够可靠
2. 滚动可能不触发 API 请求（页面状态不确定）
3. 长文需要 `page.goto()` 导航到详情页，慢且可能触发 WAF
4. 无法精确控制分页

推荐改造为 `_sync_via_fetch`：
1. 用 `page.evaluate(fetch(...))` 直接调用 API
2. 精确控制 page 参数，逐页获取
3. 长文也用 `fetch()` 获取，无需导航
4. 整体更快、更稳定、更可控

### 7.3 降级策略

如果浏览器内 `fetch()` 方案遇到问题（如 WAF 升级检测 fetch 行为），可以降级：

1. **降级方案 A**：提取 cookie → 纯 HTTP 请求
   ```python
   cookies = {c["name"]: c["value"] for c in page.context.cookies()}
   # 用 requests/httpx 直接调用 API
   ```

2. **降级方案 B**：JS 逆向 acw_sc__v2 + 纯 HTTP
   - 使用 [acw-sc-v2.js](https://github.com/WangYihang/acw-sc-v2.js) 计算 cookie
   - 完全不需要浏览器

3. **降级方案 C**：回退到滚动拦截（当前方案）
   - 作为最后的 fallback

### 7.4 注意事项

1. **user_data_dir 持久化**：保存浏览器 session，避免每次都要登录
2. **请求频率**：每页间隔 2-4 秒随机延迟，避免触发 WAF 频率限制
3. **错误重试**：单次 fetch 失败时等待 5 秒重试，最多 3 次
4. **session 过期处理**：如果 API 返回 401/403，提示用户重新登录
5. **headless 模式**：首次登录必须 `headless=False`（需要扫码），后续可以 `headless=True`
