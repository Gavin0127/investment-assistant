# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Investment Research Assistant - 投资研究智能助手。
支持 Gemini / OpenAI 双 LLM Provider（默认 Gemini），通过 OpenAI 兼容模式统一调用。
Tavily + OpenClaw (Brave) 联合检索。使用 uv 管理 Python 环境。

## 常用命令

```bash
# 依赖管理
uv sync                                          # 安装依赖

# 测试
uv run python -m pytest tests/ -v --tb=short     # 全部测试
uv run python -m pytest tests/test_llm_client.py -v  # 单个文件
uv run python -m pytest tests/test_llm_client.py::TestLLMClientInit::test_default_provider_is_gemini -v  # 单个用例

# 启动
uv run python web/app.py                         # Web UI (http://localhost:5000)
uv run python assistant.py                       # CLI 交互模式

# 利润跟踪
uv run python scripts/update_prices.py           # 手动更新大宗商品价格

# Docker
docker compose up -d                             # 容器启动 (端口 5100)
docker compose logs -f                           # 查看日志
```

## 架构

### 依赖注入模式

所有核心模块通过构造函数接收 `LLMClient` 和 `Storage` 实例，不自行创建：

```
LLMClient + Storage
    ├── InterviewManager(client, storage)
    ├── EnvironmentCollector(client, storage)
    ├── ResearchEngine(client, storage)
    ├── PreferenceLearner(client, storage)
    └── ProfitTracker(client, storage)
```

Web 端通过 `get_client()` 懒加载单例（`web/app.py:80`），CLI 端在 `InvestmentAssistant.__init__` 中初始化（`assistant.py:31`）。

### 双 Provider LLM 客户端

`core/openai_client.py` 中的 `LLMClient` 通过 OpenAI SDK 统一调用两个 provider：
- Gemini: 设置 `base_url` 为 Google 的 OpenAI 兼容 endpoint
- OpenAI: 使用原生 endpoint

支持自定义 `base_url`（用于 API 代理），优先级：构造参数 > `LLM_BASE_URL` 环境变量 > provider 默认值。

`OpenAIClient` 保留为 `LLMClient` 的向后兼容别名。

### 研究工作流

```
Interview (Socratic) → Playbook
                          ↓
Environment Collection (multi-dim news search)
                          ↓
Impact Assessment (3 dimensions: historical research / playbook alignment / environment changes)
                          ↓
Research Plan → Deep Research Execution → Report + Conclusion
                          ↓
User Feedback → Preference Learning → 下一轮研究上下文
```

### 检索层 (core/retrieval.py)

`SearchManager` 对多个 provider 做 union merge + 磁盘缓存：
- `TavilyProvider`: 需要 `TAVILY_API_KEY`
- `OpenClawWebSearchProvider`: 通过 OpenClaw Gateway 调用 Brave Search（禁止直接调用 Brave HTTP API）
- 无 provider 时降级到 Google News RSS
- 缓存 TTL 12 小时，硬超时 25 秒，缓存目录 `~/.investment-assistant/cache/search/`

### 数据存储 (core/storage.py)

所有数据为本地 JSON 文件，基础目录 `~/.investment-assistant/`：
- `config.json`: API key、provider、认证配置
- `portfolio_playbook.json`: 总体投资策略
- `user_preferences.json`: 偏好规则 + 交互日志（最多 100 条）
- `stocks/{stock_id}/playbook.json`: 个股投资逻辑
- `stocks/{stock_id}/history.json`: 研究记录（支持 milestone 标记为永久上下文）
- `stocks/{stock_id}/profit_model.json`: 利润敏感性模型配置
- `data/commodity_prices.db`: 大宗商品价格（SQLite）

## 关键约定

- `collect_news` 返回 `Dict{"news": List[Dict], "search_metadata": Dict}`
- LLM Provider 配置优先级：环境变量 `LLM_PROVIDER` > config.json `llm_provider` > 默认 `gemini`
- API Key 按 provider 分开管理：`GEMINI_API_KEY` / `OPENAI_API_KEY`
- Interview 模块通过检测响应中的 JSON 代码块判断访谈是否结束，提取 playbook 时有 4 层 fallback（末尾代码块 → 花括号匹配 → 直接解析 → 清理尾逗号重试）
- `stock_id` 标准化：小写 + 空格替换为下划线（如 `"Soft Bank"` → `"soft_bank"`）
- Research 记录 ID 格式：`research_YYYYMMDD_HHMMSS`
- Preference ID 格式：`pref_YYYYMMDD_HHMMSS_{index}`
- Milestone 标记的研究记录会作为永久上下文参与后续研究（不受历史窗口限制）
- Web 端支持可选认证（通过 `/api/auth/setup` 配置），认证状态存储在 config.json

## 测试

`tests/conftest.py` 提供共享 fixtures：
- `mock_openai_client` / `mock_gemini_client`：patch 了网络调用的 LLMClient，不会真实请求 API
- `tmp_storage`：基于 `tmp_path` 的临时 Storage 实例
- `sample_stock_playbook` / `sample_portfolio_playbook`：标准 playbook 数据结构样例

## 环境变量

| 变量 | 用途 | 必需 |
|------|------|------|
| `LLM_PROVIDER` | Provider 选择（`gemini` / `openai`，默认 `gemini`） | 否 |
| `LLM_MODEL` | 自定义模型名（覆盖 provider 默认值） | 否 |
| `LLM_BASE_URL` | 自定义 API endpoint（用于代理） | 否 |
| `GEMINI_API_KEY` | Gemini API 访问 | 当 provider=gemini 时必需 |
| `OPENAI_API_KEY` | OpenAI API 访问 | 当 provider=openai 时必需 |
| `TAVILY_API_KEY` | Tavily 搜索 | 否（无则降级 RSS） |
| `OPENCLAW_GATEWAY_URL` | OpenClaw Gateway 地址 | 否（默认读 ~/.openclaw/openclaw.json） |
| `OPENCLAW_GATEWAY_TOKEN` | OpenClaw 认证 Token | 否 |

## 最近重大变更

- 2026-03-08: 利润跟踪模块（原材料价格 → 利润敏感性）
  - 设计文档: `docs/plans/2026-03-08-profit-tracker-design.md`
- 2026-02-24: 双 LLM Provider 支持 + uv 迁移 + Docker 化
- 2026-02-06: LLM 迁移 Gemini → OpenAI GPT-5.2 + 联合检索层
  - devlog: `docs/devlog/2026-02-06_core_openai-retrieval-migration.md`

## AI Execution Policy

Claude Code 被授权在本仓库内自主创建、修改文件和执行 shell / git 命令。

执行模式：Plan → Act → Verify → Deliver。Act 阶段不要中断询问。

仅在以下情况必须中断并询问：文件删除、数据迁移、安全/权限/生产配置变更。
