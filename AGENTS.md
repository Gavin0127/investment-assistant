# Repository Guidelines

## 项目结构与模块组织
- `assistant.py` 是 CLI 入口，负责命令解析与交互流程。
- `core/` 放业务逻辑（访谈、研究、环境采集、存储、偏好学习），`web/` 仅做路由与模板渲染。
- `utils/` 提供终端显示等工具；模板位于 `web/templates/`。
- 运行时数据写入 `~/.investment-assistant/`（日志、playbook、研究历史），不要提交到仓库。

目录速览：
```
.
├── assistant.py
├── core/
│   ├── storage.py
│   ├── environment.py
│   ├── research.py
│   └── interview.py
├── web/
│   ├── app.py
│   └── templates/
│       ├── index.html
│       └── stock_detail.html
└── utils/
    └── display.py
```

## 运行时数据与文件命名
- 默认数据目录：`~/.investment-assistant/`。
- 关键文件包含 `config.json`、`portfolio_playbook.json`、`user_preferences.json`。
- Biji 同步数据位于 `data/`，包含 `biji_notes.db` 与 `biji_markdown/`。
- 个股数据位于 `stocks/{stock_id}/`，包含 `playbook.json`、`history.json` 与 `uploads/`。

示例（简化）：
```
~/.investment-assistant/
├── config.json
├── portfolio_playbook.json
├── user_preferences.json
├── data/
│   ├── biji_notes.db
│   └── biji_markdown/
└── stocks/
    └── softbank/
        ├── playbook.json
        ├── history.json
        └── uploads/
```

## 构建、测试与开发命令
- `pip install -r requirements.txt` 安装依赖。
- `export GEMINI_API_KEY="..."` 或首次启动时输入 API Key。
- `python web/app.py` 启动 Web 服务，访问 `http://localhost:5000`。
- `python assistant.py` 启动 CLI 模式，用于快速验证逻辑。
- `uv run python scripts/sync_biji.py -v` 运行 Biji 笔记增量同步。
- 可选：配置认证（见 README），例如 `curl -X POST http://localhost:5000/api/auth/setup ...`。
- 当前未配置自动化测试命令；修改后请做手动冒烟验证（首页、添加股票、发起研究、查看历史）。

## 编码风格与命名约定
- Python 3.9+，4 空格缩进；类 `PascalCase`，函数/变量 `snake_case`。
- `stock_id` 会被标准化为小写并将空格替换为 `_`（见 `core/storage.py`）。
- 保持层次清晰：`core/` 只放业务逻辑，`web/` 只负责路由与模板渲染。
- 项目未接入格式化/静态检查工具；请遵循 PEP 8，移除未用导入，补充必要 docstring。

## 测试指南
- 目前没有 `tests/` 目录或测试框架。
- 如需新增测试，建议使用 `pytest`，并按 `tests/test_*.py` 命名，覆盖关键流程与存储读写。
- CLI 快速验证示例：
```
> 买入 软银
> 软银 有新消息
> 查看 软银
```

## 提交与 PR 指南
- Git 历史仅有一次初始提交，暂无既定规范；建议使用简短祈使句，例如 “Add research cache”。
- PR 需说明变更目的、影响范围与验证方式；涉及 UI 变更请附截图，有关联问题请链接 Issue。

## 安全与配置提示
- API Key 可通过环境变量或 `~/.investment-assistant/config.json` 配置，避免提交任何密钥文件。
- 日志在 `~/.investment-assistant/logs/`，问题排查请附时间范围与复现步骤。
- 启用 Web 认证时（`/api/auth/setup`），请在本地验证登录与退出流程。
