# Biji 笔记同步设计

## 目标

为 `https://www.biji.com/note` 提供一条可重复执行的同步链路：

- 首次执行时全量抓取当前账号下正常可见的全部笔记
- 后续执行时仅增量同步新增或变更笔记
- 同时生成两套本地产物
  - 结构化数据：SQLite
  - 离线阅读：Markdown + 图片附件

## 范围

### 包含

- 当前账号下正常可见的笔记
- 笔记列表元数据
- 笔记详情正文
- 正文引用的图片附件
- 本地增量同步状态

### 不包含

- 回收站/已删除笔记
- 分享页副本
- 导入导出任务产物
- 标签聚合页结果
- Web 管理页面
- 远端删除后自动删除本地文件

## 约束

- Bearer Token 不写死在仓库中，只从本地 `~/.investment-assistant/config.json` 读取
- 同步链路优先走 HTTP API，不以浏览器自动化作为首选路径
- 第一版优先保证“抓全、增量稳定、离线可读”，不做额外管理功能

## 方案选择

评估过三种方案：

1. API 直连同步
2. 浏览器驱动抓取
3. API 优先，浏览器兜底

最终选择方案 1，并在实现层留出薄扩展点，必要时再补浏览器兜底。原因：

- 已有 Bearer Token，适合直接走接口
- 列表翻页、详情补全、增量判断更容易稳定实现
- 测试成本和维护成本显著低于浏览器抓取

## 架构

新增一条与雪球模块平行、互不耦合的 Biji 同步链路。

### 模块划分

- `core/biji_client.py`
  - 负责 Bearer 鉴权
  - 封装列表、详情、资源下载请求
  - 统一重试、超时和错误映射
- `core/biji_db.py`
  - 负责 SQLite schema 初始化
  - 负责笔记、附件、同步状态读写
- `core/biji_sync.py`
  - 负责编排“扫描列表 -> 补详情 -> 下载资源 -> 导出 Markdown”
  - 提供首次全量和后续增量的一致入口
- `scripts/sync_biji.py`
  - CLI 入口
  - 适合手工运行和后续 cron/任务编排

## 本地目录

沿用现有 `~/.investment-assistant/` 目录规范。

```text
~/.investment-assistant/
├── config.json
└── data/
    ├── biji_notes.db
    ├── biji_markdown/
    │   └── <note_id>/
    │       ├── index.md
    │       └── assets/
    └── biji_raw/
```

### 配置结构

```json
{
  "biji": {
    "enabled": true,
    "api_base": "https://notes-api.biji.com",
    "bearer_token": "YOUR_TOKEN",
    "page_size": 50,
    "download_images": true
  }
}
```

## 数据模型

### `notes`

保存笔记主数据：

- `note_id`
- `title`
- `summary`
- `raw_content`
- `markdown_content`
- `source_url`
- `created_at`
- `updated_at`
- `saved_at`
- `content_hash`
- `missing_from_remote`
- `last_exported_at`

### `note_assets`

保存附件关系：

- `note_id`
- `asset_url`
- `asset_type`
- `mime_type`
- `filename`
- `local_path`
- `download_status`
- `etag`
- `last_modified`
- `saved_at`

### `sync_state`

保存全局同步状态：

- `last_successful_sync_at`
- `latest_list_cursor`
- `initial_full_sync_done`
- `last_error`
- `last_error_at`

### `api_snapshots`

保存有限原始样本，便于排障：

- `scope`
- `entity_id`
- `payload`
- `saved_at`

## 同步流程

同步分为两个阶段。

### 阶段 1：列表增量扫描

- 调用笔记列表接口，按更新时间倒序翻页
- 逐条读取列表中的基础元数据
- 对每条记录做增量判定
  - 本地不存在：加入详情补全队列
  - 本地存在且 `updated_at` 变化：加入详情补全队列
  - 本地存在但 `updated_at` 不可靠时：退化为 `content_hash` 判定
- 当连续多页都只包含本地已同步且未变化的旧笔记时，停止翻页

### 阶段 2：详情补全与导出

- 对补全队列逐条拉取详情
- 保存原始正文和标准化 Markdown
- 解析正文中的图片链接
- 下载图片到 `assets/`
- 将 Markdown 中的远程图片地址替换为相对路径
- 更新导出时间、资源状态和内容哈希

## 增量判定

按以下优先级判断笔记是否变化：

1. 服务端 `updated_at`
2. 标准化正文的 `content_hash`

不使用标题或摘要单字段作为唯一变化依据，避免漏同步。

## Markdown 导出规则

每篇笔记导出到：

```text
~/.investment-assistant/data/biji_markdown/<note_id>/index.md
```

规则如下：

- 文件头写 YAML frontmatter
  - `note_id`
  - `title`
  - `created_at`
  - `updated_at`
  - `source_url`
- 正文仅做必要的 HTML/富文本到 Markdown 规范化
- 不做“智能改写”或摘要重写
- 图片保存到同目录 `assets/`
- Markdown 中统一使用相对路径引用本地图片
- 若图片下载失败，Markdown 保留原始远程 URL
- 若标题为空，目录名仍使用 `note_id`，frontmatter 保留真实字段

## 错误处理

### 鉴权错误

- `401/403` 直接终止本轮同步
- 错误写入 `sync_state.last_error`
- 提示更新本地配置中的 Bearer Token

### 临时性错误

- `429/5xx` 做最多 3 次指数退避重试
- 单条详情失败记入失败队列，不拖垮整轮同步

### 资源下载失败

- 不影响正文入库
- `note_assets.download_status` 标记失败
- 下次同步允许重试

### 字段漂移

- 保存失败样本到 `api_snapshots`
- 标记当前笔记失败
- 整轮同步继续处理其他笔记

## 删除策略

如果某条笔记在远端列表中不可见：

- 第一版只在 `notes.missing_from_remote` 中打标
- 不物理删除本地 Markdown 和附件

这样可以避免因接口异常、权限变化或短期数据不一致导致误删本地归档。

## 测试策略

### 单元测试

覆盖以下能力：

- 列表解析
- 详情解析
- 增量判定
- Markdown 转换
- 图片链接重写
- 错误重试

### 集成测试

使用 mocked HTTP 响应验证：

- 首次全量同步
- 二次增量同步
- Markdown 与图片落盘
- DB 与导出内容一致

### 手工冒烟

- 运行一次首轮同步，确认本地生成 DB、Markdown、图片目录
- 再运行一次增量同步，确认只更新新增或变更笔记
- 随机打开若干 `index.md`，确认图片可离线显示

## 改动范围

- Create: `core/biji_client.py`
- Create: `core/biji_db.py`
- Create: `core/biji_sync.py`
- Create: `scripts/sync_biji.py`
- Create: `tests/test_biji_client.py`
- Create: `tests/test_biji_db.py`
- Create: `tests/test_biji_sync.py`
- Modify: `core/storage.py`
- Modify: `README.md`

## 后续演进

如后续确认某些字段只能通过前端页面拿到，可在 `biji_client` 之上补一个浏览器兜底实现，但不作为第一版交付内容。
