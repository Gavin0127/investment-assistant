# Biji 混合检索与 Claude Code Skill 设计

## 背景

当前 Biji 笔记已经稳定同步到本地：

- 结构化数据在 `~/.investment-assistant/data/biji_notes.db`
- 离线导出在 `~/.investment-assistant/data/biji_markdown/`

用户的新目标不是在本项目内增加一个搜索 UI，而是让外部 `Claude Code` 能直接访问这些本地数据，并在查到所有相关资料后自行完成整理与总结。

这带来两个明确要求：

- 检索必须同时支持关键词命中与语义召回
- 最终要提供一个 user-level skill 给 `Claude Code` 使用，但该 skill 只负责返回相关 chunks 和原文路径，不负责总结

## 目标

构建一套面向本地资料库的混合检索层，使 `Claude Code` 可以：

- 查询 Biji 笔记的相关 chunks
- 获取 chunk 对应的 `note_id`、标题、章节类型和 Markdown 原文路径
- 在精确关键词和语义相似两种召回路径之间做混合检索
- 对变更过的 Biji 数据做增量索引更新

## 非目标

本次不做以下事项：

- Web 搜索页面或聊天界面
- 在线服务化 API
- 让 skill 负责自动总结、自动归纳、自动写报告
- 把 SQLite 替换成向量数据库作为主存储
- 多用户共享部署

## 约束

- 外部 `Claude Code` 直接读本地 SQLite/Markdown，不通过 HTTP API
- 可以接受调用外部 embedding API
- 检索结果必须能回溯到本地原文
- 现有 `biji_notes.db` 继续作为主数据真相源

## 方案评估

### 方案 1：仅使用现有 SQLite + FTS

- 在 `biji_notes.db` 内增加 FTS5 索引
- 只做标题、正文、摘要的全文检索

优点：

- 最简单
- 不增加额外依赖
- `Claude Code` 直接跑 SQL 即可

缺点：

- 同义表达、隐含主题、跨笔记语义召回弱
- 不能很好解决“找到所有相关数据”的目标

### 方案 2：纯向量数据库

- 只保留笔记主数据在 SQLite
- 另建独立向量库做召回
- 检索时完全靠 embedding

优点：

- 语义召回能力强

缺点：

- 对精确关键词、实体名、标题命中不稳定
- 解释性弱
- 与现有 SQLite/Markdown 容易出现一致性维护问题

### 方案 3：SQLite 主数据 + FTS5 + 本地向量侧车

- `biji_notes.db` 继续保存主数据
- 在 SQLite 内增加 FTS5 和 chunk 元数据
- 向量索引放本地侧车，优先 `LanceDB`
- 查询时走关键词与语义双路召回，再合并重排

优点：

- 同时满足精确检索和语义召回
- 保留原文可验证性
- 最适合给本地 `Claude Code` 使用
- 不需要引入服务型向量数据库

缺点：

- 比单一路径实现更复杂

最终选择方案 3。

## 总体架构

整体分三层：

### 1. 主数据层

- `~/.investment-assistant/data/biji_notes.db`
- `~/.investment-assistant/data/biji_markdown/`

继续作为真相源，保存完整笔记结构、章节语义和离线导出结果。

### 2. 检索层

- SQLite FTS5
- chunk 元数据表
- 本地向量侧车，优先 `LanceDB`

负责对 Biji 笔记做 chunk 化、全文索引、embedding 生成和混合召回。

### 3. 调用层

- `scripts/index_biji_notes.py`
- `scripts/search_biji_notes.py`
- Claude Code user-level skill

skill 不直接实现检索逻辑，只负责调用本地检索脚本并返回结构化结果。

## 数据模型

### SQLite 内新增 `notes_fts`

基于 `notes` 表做 FTS5 索引，建议覆盖字段：

- `title`
- `summary`
- `original_content`
- `ai_summary_content`
- `display_content`

作用：

- 精确关键词召回
- 标题命中增强
- 作为混合检索中的一条召回通路

### SQLite 内新增 `note_chunks`

建议字段：

- `chunk_id`
- `note_id`
- `chunk_index`
- `section_type`
- `text`
- `token_estimate`
- `char_start`
- `char_end`
- `content_hash`
- `markdown_path`
- `saved_at`

说明：

- `section_type` 取值建议：
  - `original_content`
  - `ai_summary_content`
  - `native_body`
  - `content_excerpt`
- `markdown_path` 直接指向本地离线文件，方便 `Claude Code` 深读原文

### 向量侧车

推荐使用 `LanceDB`。

每条记录至少包含：

- `chunk_id`
- `note_id`
- `title`
- `section_type`
- `markdown_path`
- `embedding`
- `embedding_model`

SQLite 负责真相源和元数据过滤，`LanceDB` 负责语义近邻搜索。

## Chunk 策略

不对整篇笔记只做一条 embedding，而是按 chunk 建索引。

规则建议：

- `ai_note`
  - `original_content` 单独切块
  - `ai_summary_content` 按标题和自然段继续切块
- `native_note`
  - `笔记正文` 按段落切块
- `unknown`
  - `内容摘录` 按段落切块

切块目标：

- 每块约 `400-900` 中文字
- 相邻块保留少量 overlap
- 标题和章节上下文尽量保留在块内

这样可以让 `Claude Code` 命中局部相关内容，而不是整篇笔记。

## 向量方案选择

不推荐第一版上服务型库：

- `Qdrant`
- `Milvus`
- `Weaviate`
- `pgvector`

原因：

- 当前是单机、本地、面向代码代理的使用方式
- 不需要额外进程、端口和服务运维
- 增加服务层只会提高维护成本

推荐：

- SQLite 继续做主数据
- `FTS5` 做关键词检索
- `LanceDB` 做本地向量侧车

## 检索流程

### 建索引

`scripts/index_biji_notes.py` 负责：

1. 读取 `biji_notes.db`
2. 扫描每条笔记的：
   - `title`
   - `original_content`
   - `ai_summary_content`
   - `display_content`
   - `markdown_path`
3. 按规则生成 chunks
4. 写入 `note_chunks`
5. 重建或增量更新 `notes_fts`
6. 生成 embeddings 并写入 `LanceDB`

### 增量更新

使用以下信号判断是否需要重建索引：

- `notes.content_hash`
- `notes.updated_at`

只重建发生变化的笔记 chunk 和 embedding，不全量重算。

### 查询

`scripts/search_biji_notes.py` 负责：

1. 接收自然语言 query
2. 同时执行两路召回
   - SQLite FTS5
   - LanceDB 向量相似检索
3. 合并、去重、重排
4. 返回结构化结果

返回字段建议：

- `note_id`
- `title`
- `section_type`
- `score`
- `text`
- `markdown_path`

## 重排策略

第一版使用简单混合重排即可：

- 标题命中加权
- `original_content` 与 `ai_summary_content` 可配置不同权重
- 同一笔记多 chunk 命中时做轻量聚合
- 保留 FTS 精确命中优先级，避免纯向量漂移

不在第一版引入额外 reranker 模型。

## Claude Code Skill 设计

最终需要额外交付一个 user-level skill，供外部 `Claude Code` 使用。

### skill 职责

只负责：

- 接收查询意图
- 调用本地 `search_biji_notes.py`
- 返回相关 chunks 和原文路径

不负责：

- 总结
- 归纳
- 报告生成
- 内容改写

### skill 调用形态

推荐 skill 内部调用：

- `uv run python scripts/search_biji_notes.py "<query>"`

输出 JSON，供 `Claude Code` 再继续读取原文和整理总结。

### 这样设计的原因

- 检索逻辑在仓库里，可测试、可演进
- skill 保持很薄，维护成本最低
- 后续更换 embedding 模型或重排逻辑时，不需要重写 skill

## 错误处理

### embedding 失败

- 记录失败 chunk
- 不阻断已有 FTS 检索
- 下次增量更新时重试

### 向量侧车损坏或缺失

- 允许降级到 FTS-only
- 明确输出“语义召回不可用”的状态

### Markdown 路径失效

- 返回 `note_id`
- 同时保留可回退的 SQLite 主数据

## 验收标准

- 本地存在混合检索索引：
  - `notes_fts`
  - `note_chunks`
  - `LanceDB` 向量侧车
- 检索脚本能输出结构化结果
- 结果包含 chunk 文本和 `markdown_path`
- 外部 `Claude Code` 能通过 user-level skill 查到相关 chunks
- skill 只返回资料，不生成总结
- 支持增量更新，不要求每次全量重建

## 风险与取舍

- embedding 模型一旦更换，需要重建向量索引
- chunk 边界设计会直接影响召回质量，需要在真实查询中迭代
- 第一版不做复杂 reranker，召回质量先靠混合检索和简单加权支撑
