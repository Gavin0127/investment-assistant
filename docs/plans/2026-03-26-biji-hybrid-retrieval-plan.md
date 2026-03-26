# Biji 混合检索与 Claude Code Skill Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为本地 Biji 数据增加 `FTS5 + 向量侧车` 的混合检索能力，并提供一个给外部 `Claude Code` 使用的 user-level skill，只返回相关 chunks 和原文路径。

**Architecture:** 保留现有 `biji_notes.db` 和 `biji_markdown/` 作为真相源，在 SQLite 内增加 `notes_fts` 与 `note_chunks`，使用 `LanceDB` 作为本地向量侧车，查询时走关键词与语义双路召回并合并重排。`Claude Code` 不直接拼 SQL 和向量查询，而是通过仓库内的检索脚本与一个薄的 user-level skill 访问结果。

**Tech Stack:** Python 3.10+, sqlite3, FTS5, OpenAI embeddings, LanceDB, pytest, uv

---

### Task 1: 增加 Biji 检索配置读取能力

**Files:**
- Modify: `core/storage.py`
- Create: `tests/test_storage_biji_retrieval.py`

**Step 1: Write the failing test**

```python
from core.storage import Storage


def test_get_biji_retrieval_config_reads_local_settings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config(
        {
            "biji_retrieval": {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "vector_db_dir": "~/custom-biji-vectors",
                "chunk_size": 700,
                "chunk_overlap": 120,
                "top_k": 12,
            }
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_provider"] == "openai"
    assert cfg["embedding_model"] == "text-embedding-3-large"
    assert cfg["vector_db_dir"] == "~/custom-biji-vectors"
    assert cfg["chunk_size"] == 700
    assert cfg["chunk_overlap"] == 120
    assert cfg["top_k"] == 12


def test_get_biji_retrieval_config_has_safe_defaults(tmp_path):
    storage = Storage(base_dir=str(tmp_path))

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_provider"] == "openai"
    assert cfg["embedding_model"]
    assert cfg["vector_db_dir"] == str(tmp_path / "data" / "biji_vectors")
    assert cfg["chunk_size"] == 700
    assert cfg["chunk_overlap"] == 120
    assert cfg["top_k"] == 10
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_storage_biji_retrieval.py -v`

Expected: FAIL，提示 `Storage` 缺少 `get_biji_retrieval_config`

**Step 3: Write minimal implementation**

在 `core/storage.py` 增加：

```python
def get_biji_retrieval_config(self) -> Dict:
    cfg = self.get_config().get("biji_retrieval") or {}
    return {
        "embedding_provider": cfg.get("embedding_provider", "openai"),
        "embedding_model": cfg.get("embedding_model", "text-embedding-3-large"),
        "vector_db_dir": cfg.get(
            "vector_db_dir",
            str(self.base_dir / "data" / "biji_vectors"),
        ),
        "chunk_size": int(cfg.get("chunk_size", 700)),
        "chunk_overlap": int(cfg.get("chunk_overlap", 120)),
        "top_k": int(cfg.get("top_k", 10)),
    }
```

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_storage_biji_retrieval.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/storage.py tests/test_storage_biji_retrieval.py
git commit -m "feat: add biji retrieval config helpers"
```

### Task 2: 扩展 BijiDB 以支持 FTS5 和 chunk 元数据

**Files:**
- Modify: `core/biji_db.py`
- Modify: `tests/test_biji_db.py`

**Step 1: Write the failing test**

```python
def test_init_schema_creates_notes_fts_and_note_chunks(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))

    with db._get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            ).fetchall()
        }

    assert "notes_fts" in tables
    assert "note_chunks" in tables


def test_upsert_chunk_and_search_fts(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        _make_note(
            "n1",
            title="英伟达护城河分析",
            original_content="原始内容",
            ai_summary_content="Token经济与加速计算",
            display_content="Token经济与护城河",
        )
    )
    db.upsert_chunk(
        {
            "chunk_id": "n1-0001",
            "note_id": "n1",
            "chunk_index": 1,
            "section_type": "ai_summary_content",
            "text": "英伟达的Token经济和护城河分析",
            "token_estimate": 42,
            "char_start": 0,
            "char_end": 16,
            "content_hash": "chunk-hash",
            "markdown_path": "/tmp/英伟达.md",
        }
    )

    chunks = db.list_chunks("n1")
    hits = db.search_notes_fts("英伟达")

    assert chunks[0]["chunk_id"] == "n1-0001"
    assert hits[0]["note_id"] == "n1"
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_db.py -v`

Expected: FAIL，提示 schema 或 `upsert_chunk` / `search_notes_fts` 缺失

**Step 3: Write minimal implementation**

修改 `core/biji_db.py`：

- 在 `_init_schema()` 中新增：

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    note_id UNINDEXED,
    title,
    summary,
    original_content,
    ai_summary_content,
    display_content
);

CREATE TABLE IF NOT EXISTS note_chunks (
    chunk_id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    section_type TEXT NOT NULL,
    text TEXT NOT NULL,
    token_estimate INTEGER,
    char_start INTEGER,
    char_end INTEGER,
    content_hash TEXT NOT NULL,
    markdown_path TEXT NOT NULL,
    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
    FOREIGN KEY (note_id) REFERENCES notes(note_id) ON DELETE CASCADE
);
```

- 在 `upsert_note()` 后同步更新 `notes_fts`
- 新增方法：
  - `upsert_chunk`
  - `replace_chunks_for_note`
  - `list_chunks`
  - `search_notes_fts`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_db.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_db.py tests/test_biji_db.py
git commit -m "feat: add biji fts and chunk storage"
```

### Task 3: 实现 Biji chunking 逻辑

**Files:**
- Create: `core/biji_chunking.py`
- Create: `tests/test_biji_chunking.py`

**Step 1: Write the failing test**

```python
from core.biji_chunking import chunk_note


def test_chunk_ai_note_splits_original_and_summary_sections():
    chunks = chunk_note(
        {
            "note_id": "n1",
            "title": "英伟达分析",
            "content_mode": "ai_note",
            "original_content": "第一段原始内容\\n\\n第二段原始内容",
            "ai_summary_content": "### 结论\\n\\nAI总结正文",
            "export_dir_name": "英伟达分析",
        },
        chunk_size=50,
        chunk_overlap=10,
        markdown_root="/tmp/biji_markdown",
    )

    section_types = [item["section_type"] for item in chunks]
    assert "original_content" in section_types
    assert "ai_summary_content" in section_types
    assert all(item["markdown_path"].endswith("/英伟达分析/index.md") for item in chunks)


def test_chunk_unknown_note_uses_content_excerpt():
    chunks = chunk_note(
        {
            "note_id": "n2",
            "title": "未知笔记",
            "content_mode": "unknown",
            "display_content": "一段未知正文",
            "export_dir_name": "未知笔记",
        },
        chunk_size=50,
        chunk_overlap=10,
        markdown_root="/tmp/biji_markdown",
    )

    assert chunks[0]["section_type"] == "content_excerpt"
    assert chunks[0]["text"] == "一段未知正文"
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_chunking.py -v`

Expected: FAIL，提示 `core.biji_chunking` 不存在

**Step 3: Write minimal implementation**

在 `core/biji_chunking.py` 实现：

- `chunk_note(note, chunk_size, chunk_overlap, markdown_root)`
- `split_text_with_overlap(text, chunk_size, chunk_overlap)`
- `estimate_tokens(text)`

输出字段至少包含：

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

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_chunking.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_chunking.py tests/test_biji_chunking.py
git commit -m "feat: add biji chunking"
```

### Task 4: 实现 embedding 与 LanceDB 侧车

**Files:**
- Modify: `pyproject.toml`
- Create: `core/biji_vector_store.py`
- Create: `tests/test_biji_vector_store.py`

**Step 1: Write the failing test**

```python
from core.biji_vector_store import BijiVectorStore


class FakeEmbedder:
    def embed_texts(self, texts):
        return [[0.1, 0.2, 0.3] for _ in texts]


def test_upsert_and_search_vectors(tmp_path):
    store = BijiVectorStore(
        db_dir=str(tmp_path / "vectors"),
        table_name="biji_chunks",
        embedder=FakeEmbedder(),
    )
    store.upsert_chunks(
        [
            {
                "chunk_id": "n1-0001",
                "note_id": "n1",
                "title": "英伟达分析",
                "section_type": "ai_summary_content",
                "text": "英伟达 token 经济与护城河",
                "markdown_path": "/tmp/英伟达.md",
            }
        ]
    )

    hits = store.search("英伟达 护城河", top_k=3)

    assert hits[0]["chunk_id"] == "n1-0001"
    assert hits[0]["markdown_path"] == "/tmp/英伟达.md"
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_vector_store.py -v`

Expected: FAIL，提示 `core.biji_vector_store` 或 `lancedb` 缺失

**Step 3: Write minimal implementation**

- 在 `pyproject.toml` 增加 `lancedb`
- 在 `core/biji_vector_store.py` 实现：
  - `OpenAIEmbedder`
  - `BijiVectorStore`
  - `upsert_chunks`
  - `delete_chunks_for_note`
  - `search`

`OpenAIEmbedder` 用现有 `openai` 依赖生成 embeddings，默认模型从配置读取。

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_vector_store.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml core/biji_vector_store.py tests/test_biji_vector_store.py
git commit -m "feat: add biji vector sidecar"
```

### Task 5: 实现索引构建脚本

**Files:**
- Create: `scripts/index_biji_notes.py`
- Create: `tests/test_biji_index_cli.py`

**Step 1: Write the failing test**

```python
from pathlib import Path

from scripts.index_biji_notes import main


def test_index_cli_rebuilds_chunks_and_vectors(monkeypatch, tmp_path, capsys):
    state = {}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_retrieval_config(self):
            return {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "vector_db_dir": str(self.base_dir / "data" / "biji_vectors"),
                "chunk_size": 700,
                "chunk_overlap": 120,
                "top_k": 10,
            }

    class FakeIndexer:
        def __init__(self, **kwargs):
            state["kwargs"] = kwargs

        def rebuild(self):
            return {"notes_indexed": 5, "chunks_indexed": 12}

    monkeypatch.setattr("scripts.index_biji_notes.Storage", FakeStorage)
    monkeypatch.setattr("scripts.index_biji_notes.BijiIndexBuilder", FakeIndexer)

    exit_code = main(["--base-dir", str(tmp_path), "--rebuild"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert "notes_indexed=5" in out
    assert "chunks_indexed=12" in out
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_index_cli.py -v`

Expected: FAIL，提示脚本不存在

**Step 3: Write minimal implementation**

实现：

- `core/biji_index_builder.py`
  - 负责扫 `notes`
  - 调 `chunk_note`
  - 写入 `note_chunks`
  - 更新 `LanceDB`
- `scripts/index_biji_notes.py`
  - 支持 `--rebuild`
  - 支持 `--base-dir`
  - 输出 `notes_indexed/chunks_indexed`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_index_cli.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_index_builder.py scripts/index_biji_notes.py tests/test_biji_index_cli.py
git commit -m "feat: add biji indexing cli"
```

### Task 6: 实现混合检索脚本

**Files:**
- Create: `core/biji_search.py`
- Create: `scripts/search_biji_notes.py`
- Create: `tests/test_biji_search_cli.py`

**Step 1: Write the failing test**

```python
from scripts.search_biji_notes import main


def test_search_cli_returns_json_results(monkeypatch, capsys):
    class FakeSearchService:
        def __init__(self, **kwargs):
            pass

        def search(self, query, top_k=10):
            return {
                "query": query,
                "results": [
                    {
                        "note_id": "n1",
                        "title": "英伟达分析",
                        "section_type": "ai_summary_content",
                        "score": 0.91,
                        "text": "英伟达 token 经济与护城河",
                        "markdown_path": "/tmp/英伟达.md",
                    }
                ],
            }

    monkeypatch.setattr("scripts.search_biji_notes.BijiHybridSearchService", FakeSearchService)

    exit_code = main(["英伟达 护城河"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert '"note_id": "n1"' in out
    assert '"markdown_path": "/tmp/英伟达.md"' in out
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_search_cli.py -v`

Expected: FAIL，提示脚本或 service 不存在

**Step 3: Write minimal implementation**

实现：

- `core/biji_search.py`
  - `BijiHybridSearchService`
  - `search(query, top_k=10)`
  - 内部流程：
    - SQLite FTS5 搜索
    - LanceDB 搜索
    - 合并去重
    - 简单重排
- `scripts/search_biji_notes.py`
  - 接收 query
  - 输出 JSON

JSON 输出结构必须至少包含：

- `note_id`
- `title`
- `section_type`
- `score`
- `text`
- `markdown_path`

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_search_cli.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_search.py scripts/search_biji_notes.py tests/test_biji_search_cli.py
git commit -m "feat: add biji hybrid search cli"
```

### Task 7: 创建 Claude Code user-level skill

**Files:**
- Create: `/Users/gxt/.codex/skills/biji-hybrid-search/SKILL.md`
- Create: `/Users/gxt/.codex/skills/biji-hybrid-search/README.md`

**Step 1: Write the failing test**

这一步没有仓库内自动化测试，先定义验收方式：

- skill 必须明确说明：
  - 只负责检索
  - 不负责总结
  - 内部调用 `scripts/search_biji_notes.py`
- Claude Code 读到 skill 后，能把查询结果返回为 chunk + 原文路径

**Step 2: Run test to verify it fails**

Run:

```bash
test -f /Users/gxt/.codex/skills/biji-hybrid-search/SKILL.md
```

Expected: FAIL，文件不存在

**Step 3: Write minimal implementation**

创建 `SKILL.md`，内容至少包含：

- 触发场景：
  - 用户要求搜索本地 Biji 笔记
  - 用户要求找相关 chunks
  - 用户要求按主题搜资料后自行总结
- 执行方式：

```bash
uv run python /Users/gxt/CODE/opensource/investment-assistant/scripts/search_biji_notes.py "<query>"
```

- 返回约束：
  - 只返回相关 chunks 和 `markdown_path`
  - 不生成总结
  - 不重写资料内容

`README.md` 简要说明：

- 依赖本地仓库与本地 `biji_notes.db`
- 使用前先跑 `index_biji_notes.py`

**Step 4: Run test to verify it passes**

Run:

```bash
test -f /Users/gxt/.codex/skills/biji-hybrid-search/SKILL.md
rg -n "search_biji_notes.py|不负责总结|markdown_path" /Users/gxt/.codex/skills/biji-hybrid-search/SKILL.md
```

Expected: PASS

**Step 5: Commit**

这一步只提交仓库内引用到 skill 的文档，不提交家目录下 skill 文件本身。

```bash
git add README.md AGENTS.md
git commit -m "docs: add biji retrieval usage"
```

### Task 8: 补文档并做端到端验证

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Step 1: Write the failing test**

Run:

```bash
rg -n "index_biji_notes.py|search_biji_notes.py|biji-hybrid-search|LanceDB|FTS5" README.md AGENTS.md
```

Expected: 搜不到完整的新说明

**Step 2: Run test to verify it fails**

同上，当前输出不完整

**Step 3: Write minimal implementation**

在文档中增加：

- 检索层架构说明
- 建索引命令：

```bash
uv run python scripts/index_biji_notes.py --rebuild
```

- 查询命令：

```bash
uv run python scripts/search_biji_notes.py "英伟达 护城河 token 经济"
```

- skill 使用说明

**Step 4: Run test to verify it passes**

Run:

```bash
uv run python -m pytest
uv run python scripts/index_biji_notes.py --rebuild
uv run python scripts/search_biji_notes.py "英伟达 护城河 token 经济"
```

Expected:

- 全量测试通过
- 索引构建成功
- 查询返回 JSON 结果，且包含 `text` 与 `markdown_path`

**Step 5: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: describe biji hybrid retrieval"
```
