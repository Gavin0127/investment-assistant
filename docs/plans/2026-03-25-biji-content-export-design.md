# Biji 正文导出纠偏设计

## 背景

当前 Biji 同步链路已经完成基础抓取、SQLite 入库、Markdown 导出和浏览器会话鉴权，但离线结果与目标仍有两个明显偏差：

- 导出的正文更接近 Biji 的整理后内容，不是用户期望保留的原始 context
- 导出目录使用 `note_id`，不便于按标题浏览

用户补充后的目标是：

- AI 笔记同时保留两层内容
  - `原始内容`
  - `AI 总结（需验证可信度）`
- 原生笔记只保留一层内容
  - `笔记正文`
- 结构化数据和 Markdown 都要体现这层语义拆分
- 本地现有 Biji 数据允许整体清理后重建

## 目标

为 Biji 笔记导出链路增加内容语义纠偏能力，确保本地结果满足以下要求：

- SQLite 显式保存原始内容、AI 总结、正文类型和导出目录名
- Markdown 按正文类型导出正确章节标题
- 导出目录改为基于标题命名，必要时以 `note_id` 兜底
- 旧格式本地数据可以安全清理并按新模型全量重建

## 非目标

本次不处理以下事项：

- 站外链接正文穿透抓取
- 回收站、分享页副本、已删除笔记同步
- Web 管理界面改造
- 历史数据库平滑迁移

## 关键发现

在当前实现与样例数据中，已经确认：

- `core/biji_client.py` 现在只解析详情接口中的 `content` 和 `body_text`
- 当前 `biji_raw/*.json` 保存的是解析后的结构，不是服务端完整原始响应
- 当前 Markdown 导出路径为 `biji_markdown/<note_id>/index.md`
- 至少部分真实笔记的 `content` 呈现为结构化总结，而不是逐字原文
- `/web` 页面被用户指出可能包含原文信息，但本机验证尚未证明其对所有笔记都稳定可用

因此，本次设计必须允许：

- 正文内容来自多个信息源
- `原始内容` 缺失时按规则留空
- 无法证明为 AI 笔记时不误标 `AI 总结（需验证可信度）`

## 方案评估

评估过三种方案：

### 方案 1：只改现有详情接口字段映射

- 继续只依赖详情 API
- 将 `content`、`body_text` 重新命名后导出

优点：

- 改动最小
- 同步和测试成本最低

缺点：

- 无法解决“当前内容并非原始 context”的核心问题
- 会把错误语义继续固化到本地数据

### 方案 2：完全切到页面抓取

- 列表、详情、正文都改为浏览器页面提取
- 从 `/web` 或标准详情页 DOM 中抽取全部内容

优点：

- 贴近用户在产品里看到的最终展示

缺点：

- 易受前端结构变更影响
- 增量同步、附件处理、测试稳定性都明显变差

### 方案 3：API 做索引，页面做正文判型与补充

- 列表、基础元数据、附件继续走现有 API
- 正文层新增页面信息源，用于判型和补充正文语义
- 统一生成结构化字段与 Markdown

优点：

- 保留现有同步稳定性
- 能针对正文语义单独纠偏
- 限制改动范围，不推翻现有链路

缺点：

- 实现复杂度高于单一路径
- 需要维护一套内容判型逻辑

最终选择方案 3。

## 数据模型

`notes` 表从“单正文”调整为“语义分层正文”。建议字段如下：

- `note_id`
- `title`
- `summary`
- `content_mode`
- `original_content`
- `ai_summary_content`
- `display_content`
- `content_source`
- `raw_content`
- `markdown_content`
- `source_url`
- `created_at`
- `updated_at`
- `content_hash`
- `missing_from_remote`
- `last_exported_at`
- `export_dir_name`
- `saved_at`

说明：

- `content_mode`
  - `ai_note`
  - `native_note`
  - `unknown`
- `original_content`
  - 原始 context / 原文，拿不到则为空
- `ai_summary_content`
  - 仅在 AI 笔记时保存
- `display_content`
  - 标准化后的最终展示正文，用于统一检索和调试
- `content_source`
  - 记录正文来自 `api_detail`、`web_page` 或 `mixed`
- `export_dir_name`
  - 当前导出目录名，避免标题变化后无法跟踪

`note_assets` 保持现有结构，不新增站外正文抓取逻辑。站外链接继续只记录，不穿透抓取。

## 内容判型规则

每条笔记在详情补全阶段做一次正文判型。

### `ai_note`

满足以下条件之一时标记为 `ai_note`：

- 页面或对应数据源中可同时识别“原始内容”和整理后的内容
- 现有字段或页面结构明确体现为 AI 笔记

写入规则：

- `original_content` 写原始 context
- `ai_summary_content` 写整理后内容
- `display_content` 由两段内容拼装得到

### `native_note`

满足以下条件时标记为 `native_note`：

- 只有一层正文
- 内容形态明显是原生笔记正文
- 不存在独立原始 context 与 AI 总结分层

写入规则：

- `original_content` 置空
- `ai_summary_content` 置空
- `display_content` 写正文本体

### `unknown`

无法稳定判定时标记为 `unknown`。

写入规则：

- 保守保留可确认的正文
- 不生成误导性的 `AI 总结（需验证可信度）`
- 原始内容允许留空

## Markdown 导出规范

### AI 笔记

导出结构：

```markdown
---
note_id: "..."
title: "..."
content_mode: "ai_note"
source_url: "..."
created_at: "..."
updated_at: "..."
---

# 标题

## 原始内容

...

## AI 总结（需验证可信度）

...
```

规则：

- `原始内容` 标题始终保留
- 拿不到原始内容时，章节正文留空
- 仅 AI 笔记出现 `AI 总结（需验证可信度）`

### 原生笔记

导出结构：

```markdown
---
note_id: "..."
title: "..."
content_mode: "native_note"
source_url: "..."
created_at: "..."
updated_at: "..."
---

# 标题

## 笔记正文

...
```

规则：

- 不出现 `AI 总结（需验证可信度）`
- 只有一层正文标题 `笔记正文`

## 导出目录命名

Markdown 目录由标题生成，不再默认使用 `note_id`。

规则：

- 默认：`<清洗后的标题>`
- 标题为空：`未命名笔记-<note_id>`
- 标题重名：`<清洗后的标题>-<note_id>`
- 非法字符移除或替换
  - `/ \\ : * ? " < > |`
- 压缩连续空白
- 截断到合理长度，避免极长路径

当标题变化导致目录名变化时：

- 更新 `notes.export_dir_name`
- 重新导出到新目录
- 清理旧目录，避免残留旧名称

## 同步与重建流程

本次不做历史迁移，直接做一次破坏式重建。

重建前：

- 删除 `~/.investment-assistant/data/biji_notes.db`
- 删除 `~/.investment-assistant/data/biji_markdown/`
- 删除 `~/.investment-assistant/data/biji_raw/`
- 保留 `~/.investment-assistant/data/biji_browser/`

重建流程：

1. 使用现有列表接口全量拉取全部笔记元数据
2. 对每条笔记拉取详情 API 数据
3. 补取页面级正文信息
4. 做正文判型与字段拆分
5. 写入 SQLite
6. 下载站内附件并替换 Markdown 中的引用
7. 用标题目录名导出 Markdown
8. 保存原始响应与抽取结果到 `biji_raw/`
9. 全量结束后再跑一轮增量校验

## 原始快照策略

`biji_raw/` 不再只保存解析后的 detail 结果，改为保存三类信息：

- API 原始 detail 响应
- 页面正文抽取结果
- 统一标准化后的结构化结果

这样后续若判型逻辑需要调整，可以基于快照回放排查，而不是再次全站抓取。

## 错误处理

### 正文信息不完整

- 若拿不到 `original_content`
  - AI 笔记保留空白 `原始内容` 章节
  - 原生笔记直接只导出 `笔记正文`

### 页面信息源不可用

- 不中断整轮同步
- 回退到详情 API 的可确认字段
- 将 `content_mode` 标为 `unknown` 或按保守规则落正文

### 标题目录名冲突

- 自动追加 `note_id`
- 不覆盖其他笔记目录

### 站外链接资源

- 继续记录为 `external`
- 不计为同步失败

## 验收标准

- AI 笔记导出的 Markdown 同时包含
  - `原始内容`
  - `AI 总结（需验证可信度）`
- 原生笔记导出的 Markdown 只包含
  - `笔记正文`
- SQLite 中存在新的语义字段并正确填充
- 导出目录基于标题命名，不再是纯 `note_id`
- 本地旧格式数据被清空，不残留旧目录结构
- 全量重建后第二轮同步结果稳定为
  - `created=0 updated=0 skipped=N failed=0`

## 风险与取舍

- `/web` 页面是否始终可用尚未被本机完全验证，因此实现上必须允许回退
- 不做历史迁移意味着首次重建会重新下载并重写本地所有 Biji 数据
- 判型逻辑需要以保守为先，宁可少标 `ai_note`，也不误把原生笔记写成 AI 总结
