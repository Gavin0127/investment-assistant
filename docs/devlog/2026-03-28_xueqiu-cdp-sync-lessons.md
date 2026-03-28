# 2026-03-28: 雪球同步经验沉淀（CDP 优先 + 原生浏览器回退）

## 背景

在修复 `Tbills (8099408395)` 的雪球同步时，遇到了两个连续问题：

1. `patchright` 默认拉起的内置 Chromium 在当前 macOS 环境中不稳定，`launch_persistent_context()` 会直接退出，报 `Mach rendezvous failed` / `SIGTRAP` / `Target page, context or browser has been closed`
2. 即使切换到可启动的 Chrome for Testing，使用独立 profile 时，雪球深页接口仍然可能返回：

```json
{"error_description":"请登录雪球查看更多内容","error_code":"10022"}
```

这说明“能拿到首页和第 1 页”不等于“深页权限已经具备”。

## 核心结论

这次验证下来，最稳定的方案不是单纯“自拉起一个浏览器”，而是：

1. **优先复用用户正在使用的真实 Chrome 会话（CDP）**
2. **连不上 CDP 时，再回退到项目自己拉起的持久化浏览器 profile**

原因：

- 真实 Chrome 已经包含用户刚完成的登录态和滑块验证结果
- 通过 `ws://127.0.0.1:9222/devtools/browser/...` 接入后，项目侧可以直接复用同一个浏览器上下文
- 在本次验证中，CDP 复用主 Chrome 后，`/v4/statuses/user_timeline.json?page=2/3` 能稳定返回 JSON；独立会话则容易在深页掉成 `10022`

## 实际验证结果

### 失败路径

- `patchright` 内置 Chromium：浏览器直接崩溃，无法进入同步逻辑
- 独立 profile + 自启动 Chrome：第 1 页可返回，深页经常报 `10022`

### 成功路径

先启动主 Chrome 的 CDP：

```bash
open -na 'Google Chrome' --args \
  --remote-debugging-port=9222 \
  --user-data-dir="$HOME/.investment-assistant/data/xueqiu_cdp_browser" \
  --no-first-run \
  --no-default-browser-check \
  "https://xueqiu.com/u/8099408395"
```

用户在这个真实 Chrome 中完成登录后，项目原生脚本：

```bash
./.venv/bin/python scripts/sync_xueqiu.py --user-id 8099408395 -v
```

会自动探测 `http://127.0.0.1:9222/json/version`，解析 `webSocketDebuggerUrl`，优先走 CDP 复用主 Chrome。

## 代码层变更

### `core/xueqiu_scraper.py`

- 新增 `_resolve_cdp_ws_url()`
  - 优先读取 `XUEQIU_CDP_WS_URL`
  - 否则默认请求 `XUEQIU_CDP_HTTP_URL`（默认 `http://127.0.0.1:9222`）的 `/json/version`
- 新增 `_open_browser_session()`
  - `CDP` 可用时：`connect_over_cdp()`
  - 不可用时：回退到 `launch_persistent_context()`
- 新增 `_close_browser_session()`
  - CDP 模式只关闭本次新开的 page / browser handle，不干扰主 Chrome 进程
- 保留 `_cleanup_stale_profile_locks()`
  - 仅用于 fallback 模式清理崩溃后残留锁
- `_browser_fetch_api()` 改为：
  - **先尝试浏览器内 `fetch()`**
  - 返回 HTML/WAF 页时，再回退到 `page.goto()`
  - 如果 API 明确返回 `10022` / `400016`，抛出清晰错误

### `tests/test_xueqiu_scraper.py`

- 新增浏览器路径选择测试
- 新增 CDP 地址解析测试
- 新增会话打开策略测试（CDP 优先，launch 回退）
- 新增 WAF / 登录态错误的行为测试

## 运行经验

### 推荐工作流

1. 启动带 `9222` 的主 Chrome
2. 在该 Chrome 中登录雪球并完成滑块验证
3. 运行项目原生同步脚本或 Web 同步接口

### 当前默认行为

项目会自动：

1. 尝试连接 `http://127.0.0.1:9222/json/version`
2. 如果可用，走 CDP
3. 如果不可用，回退到项目自己的 `xueqiu_browser` 持久化 profile

### 可配置环境变量

| 变量 | 作用 |
|------|------|
| `XUEQIU_CDP_WS_URL` | 直接指定 CDP websocket 地址 |
| `XUEQIU_CDP_HTTP_URL` | 指定 CDP HTTP 基地址，默认 `http://127.0.0.1:9222` |
| `XUEQIU_CHROME_PATH` | fallback 模式下指定浏览器可执行文件 |

## 剩余问题

### 表情图片下载 warning

部分帖子正文里的 emoji 图片是协议相对地址，例如：

```text
//assets.imedao.com/ugc/images/face/emoji_35_like.png?v=1
```

当前下载器会报 `Invalid URL ... No scheme supplied`，但这不影响正文和帖子主数据入库。

后续可优化：

- 对 `//assets.imedao.com/...` 自动补全为 `https://assets.imedao.com/...`

## 结论

雪球同步的经验可以总结为一句话：

**真实登录态比“看起来像已登录”的 cookie 更重要；深页权限要优先通过 CDP 复用主 Chrome 来拿。**
