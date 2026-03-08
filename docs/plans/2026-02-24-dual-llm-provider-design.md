# Dual LLM Provider + uv 迁移设计

日期: 2026-02-24

## 目标

1. 项目同时兼容 OpenAI 和 Gemini，默认走 Gemini
2. Gemini 通过 OpenAI 兼容模式接入（不引入新 SDK）
3. Python 环境从 pip/requirements.txt 迁移到 uv

## LLM Provider 抽象

改造 `core/openai_client.py`，类重命名为 `LLMClient`：

- 构造函数根据 provider 决定 `base_url` 和 `model`：
  - `gemini` → base_url=`https://generativelanguage.googleapis.com/v1beta/openai/`, model=`gemini-3.1-flash`
  - `openai` → base_url 不设（OpenAI 默认），model=`gpt-5.2`
- API Key 按 provider 分开读取

Provider 配置优先级：
1. 环境变量 `LLM_PROVIDER`
2. `~/.investment-assistant/config.json` → `llm_provider`
3. 默认值：`gemini`

## 配置管理

`core/storage.py` 改造：

- `get_llm_provider()` — 读取 provider 配置
- `get_api_key()` — 按 provider 动态读取对应 key
- `set_api_key()` — 按 provider 写入对应字段

config.json 结构：
```json
{
  "llm_provider": "gemini",
  "llm_model": "gemini-3.1-flash",
  "gemini_api_key": "...",
  "openai_api_key": "..."
}
```

两个 provider 的 key 可同时存在，切换不丢失。

## uv 迁移

- 创建 `pyproject.toml`，Python `>=3.10`
- 依赖迁移，无新增 SDK
- 删除 `requirements.txt`
- `uv sync` 生成 lockfile

## 分支与提交

分支：`feat/dual-llm-provider`

提交拆分：
1. `chore: migrate to uv with pyproject.toml`
2. `feat(core): add dual LLM provider support (Gemini + OpenAI)`
3. `test: update tests for LLMClient`
