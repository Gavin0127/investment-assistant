# Biji 笔记同步 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `https://www.biji.com/note` 增加一条可重复执行的本地同步链路，支持首次全量抓取、后续增量同步，并同时落 SQLite 与离线 Markdown + 图片附件。

**Architecture:** 先锁定 Biji Web API 契约并保存测试夹具，再按 TDD 顺序实现配置读取、SQLite 数据层、HTTP Client、同步编排器和 CLI。同步器以列表分页扫描发现变更，再按需拉详情和图片资源，最后导出本地 Markdown，删除只打标不物理清理。

**Tech Stack:** Python 3.10+, requests, sqlite3, pytest, uv, markdownify

---

### Task 1: 锁定 Biji API 契约与测试夹具

**Files:**
- Create: `docs/plans/2026-03-24-biji-api-contract.md`
- Create: `tests/fixtures/biji/list_page_1.json`
- Create: `tests/fixtures/biji/note_detail_sample.json`

**Step 1: 从本地配置读取 Bearer Token**

Run:

```bash
TOKEN=$(uv run python - <<'PY'
import json
from pathlib import Path
cfg = json.loads((Path.home() / ".investment-assistant" / "config.json").read_text())
print((cfg.get("biji") or {}).get("bearer_token", ""))
PY
)
test -n "$TOKEN"
```

Expected: shell 返回 0，说明本地配置里已存在 `biji.bearer_token`

**Step 2: 抓取列表接口样本**

Run:

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  "https://notes-api.biji.com/voicenotes/web/notes?page=1&page_size=5" \
  > tests/fixtures/biji/list_page_1.json
```

Expected: `tests/fixtures/biji/list_page_1.json` 为合法 JSON，且包含至少 1 条笔记记录

**Step 3: 抓取详情接口样本**

Run:

```bash
NOTE_ID=$(uv run python - <<'PY'
import json
from pathlib import Path
data = json.loads(Path("tests/fixtures/biji/list_page_1.json").read_text())
items = data.get("data") or data.get("items") or data.get("list") or []
first = items[0] if items else {}
print(first.get("id") or first.get("note_id") or "")
PY
)

curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  "https://notes-api.biji.com/voicenotes/web/notes/${NOTE_ID}" \
  > tests/fixtures/biji/note_detail_sample.json
```

Expected: `tests/fixtures/biji/note_detail_sample.json` 为合法 JSON，且包含正文、更新时间、资源链接中的至少一种

**Step 4: 记录接口契约**

把以下信息写进 `docs/plans/2026-03-24-biji-api-contract.md`：

- 列表接口 URL、分页参数、排序方向
- 详情接口 URL
- 列表响应里实际使用的字段名
- 详情响应里正文、标题、更新时间、图片字段名
- 判断“未删除/正常可见”的状态字段

Run:

```bash
uv run python -m json.tool tests/fixtures/biji/list_page_1.json >/dev/null
uv run python -m json.tool tests/fixtures/biji/note_detail_sample.json >/dev/null
```

Expected: 两条命令都成功返回

**Step 5: Commit**

```bash
git add docs/plans/2026-03-24-biji-api-contract.md tests/fixtures/biji/list_page_1.json tests/fixtures/biji/note_detail_sample.json
git commit -m "test: capture biji api fixtures"
```

### Task 2: 增加 Biji 配置读取能力

**Files:**
- Modify: `core/storage.py`
- Create: `tests/test_storage_biji.py`

**Step 1: 写失败测试**

```python
from core.storage import Storage


def test_get_biji_config_reads_local_settings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "enabled": True,
            "api_base": "https://notes-api.biji.com",
            "bearer_token": "secret-token",
            "page_size": 50,
            "download_images": True,
        }
    })

    cfg = storage.get_biji_config()

    assert cfg["enabled"] is True
    assert cfg["api_base"] == "https://notes-api.biji.com"
    assert cfg["bearer_token"] == "secret-token"


def test_get_biji_token_returns_none_when_missing(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    assert storage.get_biji_token() is None
```

**Step 2: 跑测试，确认失败**

Run: `uv run python -m pytest tests/test_storage_biji.py -v`

Expected: FAIL，提示 `Storage` 缺少 `get_biji_config` 或 `get_biji_token`

**Step 3: 写最小实现**

在 `core/storage.py` 增加：

```python
def get_biji_config(self) -> Dict:
    cfg = self.get_config().get("biji") or {}
    return {
        "enabled": bool(cfg.get("enabled", False)),
        "api_base": cfg.get("api_base", "https://notes-api.biji.com"),
        "bearer_token": cfg.get("bearer_token"),
        "page_size": int(cfg.get("page_size", 50)),
        "download_images": bool(cfg.get("download_images", True)),
    }


def get_biji_token(self) -> Optional[str]:
    token = (self.get_biji_config().get("bearer_token") or "").strip()
    return token or None
```

**Step 4: 跑测试，确认通过**

Run: `uv run python -m pytest tests/test_storage_biji.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/storage.py tests/test_storage_biji.py
git commit -m "feat: add biji config helpers"
```

### Task 3: 实现 Biji SQLite 数据层

**Files:**
- Create: `core/biji_db.py`
- Create: `tests/test_biji_db.py`

**Step 1: 写失败测试**

```python
from core.biji_db import BijiDB


def test_save_note_and_asset(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note({
        "note_id": "n1",
        "title": "第一篇",
        "summary": "摘要",
        "raw_content": "<p>Hello</p>",
        "markdown_content": "Hello",
        "source_url": "https://www.biji.com/note/n1",
        "created_at": "2026-03-24T10:00:00+08:00",
        "updated_at": "2026-03-24T10:00:00+08:00",
        "content_hash": "abc",
        "missing_from_remote": 0,
    })
    db.upsert_asset({
        "note_id": "n1",
        "asset_url": "https://img.example.com/a.png",
        "asset_type": "image",
        "filename": "001.png",
        "local_path": "biji_markdown/n1/assets/001.png",
        "download_status": "done",
    })

    note = db.get_note("n1")
    assets = db.list_assets("n1")

    assert note["title"] == "第一篇"
    assert assets[0]["filename"] == "001.png"


def test_sync_state_round_trip(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.set_sync_state("initial_full_sync_done", "1")
    assert db.get_sync_state("initial_full_sync_done") == "1"
```

**Step 2: 跑测试，确认失败**

Run: `uv run python -m pytest tests/test_biji_db.py -v`

Expected: FAIL，提示 `core.biji_db` 不存在

**Step 3: 写最小实现**

在 `core/biji_db.py` 中实现：

- `BijiDB.__init__`
- `_init_schema`
- `upsert_note`
- `get_note`
- `list_notes`
- `upsert_asset`
- `list_assets`
- `set_sync_state`
- `get_sync_state`

Schema 至少包含表：

```sql
notes(note_id primary key, title, summary, raw_content, markdown_content,
      source_url, created_at, updated_at, saved_at, content_hash,
      missing_from_remote, last_exported_at)

note_assets(note_id, asset_url, asset_type, mime_type, filename,
            local_path, download_status, etag, last_modified, saved_at,
            primary key(note_id, asset_url))

sync_state(key primary key, value, updated_at)

api_snapshots(scope, entity_id, payload, saved_at)
```

**Step 4: 跑测试，确认通过**

Run: `uv run python -m pytest tests/test_biji_db.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_db.py tests/test_biji_db.py
git commit -m "feat: add biji sqlite storage"
```

### Task 4: 实现 Biji HTTP Client 与字段解析

**Files:**
- Create: `core/biji_client.py`
- Create: `tests/test_biji_client.py`
- Test Fixture: `tests/fixtures/biji/list_page_1.json`
- Test Fixture: `tests/fixtures/biji/note_detail_sample.json`

**Step 1: 写失败测试**

```python
import json
from pathlib import Path

from core.biji_client import BijiClient


def test_build_headers_includes_bearer():
    client = BijiClient(api_base="https://notes-api.biji.com", bearer_token="secret")
    headers = client._build_headers()
    assert headers["Authorization"] == "Bearer secret"


def test_parse_list_fixture_extracts_notes():
    raw = json.loads(Path("tests/fixtures/biji/list_page_1.json").read_text())
    notes = BijiClient.parse_list_response(raw)
    assert len(notes) >= 1
    assert "note_id" in notes[0]
    assert "updated_at" in notes[0]


def test_parse_detail_fixture_extracts_content():
    raw = json.loads(Path("tests/fixtures/biji/note_detail_sample.json").read_text())
    detail = BijiClient.parse_detail_response(raw)
    assert detail["note_id"]
    assert "raw_content" in detail
```

**Step 2: 跑测试，确认失败**

Run: `uv run python -m pytest tests/test_biji_client.py -v`

Expected: FAIL，提示 `core.biji_client` 不存在

**Step 3: 写最小实现**

在 `core/biji_client.py` 中实现：

- `BijiClient.__init__(api_base, bearer_token, timeout=20)`
- `_build_headers()`
- `list_notes(page, page_size)`
- `get_note_detail(note_id)`
- `download_asset(url, dest_path)`
- `parse_list_response(raw)`
- `parse_detail_response(raw)`

Client 行为要求：

- 所有请求统一携带 `Authorization: Bearer <token>`
- `401/403` 抛出明确鉴权异常
- `429/5xx` 做最多 3 次重试
- 解析函数把接口字段标准化为：

```python
{
    "note_id": "...",
    "title": "...",
    "summary": "...",
    "source_url": "...",
    "created_at": "...",
    "updated_at": "...",
    "raw_content": "...",
    "assets": [{"asset_url": "...", "asset_type": "image"}],
}
```

**Step 4: 跑测试，确认通过**

Run: `uv run python -m pytest tests/test_biji_client.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_client.py tests/test_biji_client.py tests/fixtures/biji/list_page_1.json tests/fixtures/biji/note_detail_sample.json
git commit -m "feat: add biji api client"
```

### Task 5: 实现同步编排器与 Markdown 导出

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `core/biji_sync.py`
- Create: `tests/test_biji_sync.py`

**Step 1: 写失败测试**

```python
from pathlib import Path

from core.biji_db import BijiDB
from core.biji_sync import BijiSyncService


class FakeClient:
    def __init__(self):
        self.list_calls = 0

    def list_notes(self, page, page_size):
        self.list_calls += 1
        if page == 1:
            return [{
                "note_id": "n1",
                "title": "第一篇",
                "summary": "摘要",
                "source_url": "https://www.biji.com/note/n1",
                "created_at": "2026-03-24T10:00:00+08:00",
                "updated_at": "2026-03-24T10:00:00+08:00",
            }]
        return []

    def get_note_detail(self, note_id):
        return {
            "note_id": note_id,
            "title": "第一篇",
            "summary": "摘要",
            "source_url": "https://www.biji.com/note/n1",
            "created_at": "2026-03-24T10:00:00+08:00",
            "updated_at": "2026-03-24T10:00:00+08:00",
            "raw_content": "<p>Hello <img src=\"https://img.example.com/a.png\"></p>",
            "assets": [{"asset_url": "https://img.example.com/a.png", "asset_type": "image"}],
        }

    def download_asset(self, url, dest_path):
        Path(dest_path).write_bytes(b"fake-image")


def test_first_sync_creates_db_row_and_markdown(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=FakeClient(),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()

    assert result["created"] == 1
    assert db.get_note("n1")["title"] == "第一篇"
    assert (tmp_path / "biji_markdown" / "n1" / "index.md").exists()


def test_second_sync_skips_unchanged_note(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=FakeClient(),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()
    result = service.sync_once()

    assert result["created"] == 0
    assert result["updated"] == 0
```

**Step 2: 跑测试，确认失败**

Run: `uv run python -m pytest tests/test_biji_sync.py -v`

Expected: FAIL，提示 `core.biji_sync` 不存在

**Step 3: 写最小实现**

先添加依赖：

```toml
"markdownify>=0.13.1",
```

然后在 `core/biji_sync.py` 中实现：

- `BijiSyncService.__init__`
- `sync_once()`
- `_should_refresh_note()`
- `_normalize_markdown()`
- `_export_note_markdown()`
- `_download_assets()`
- `_replace_asset_urls()`

实现要求：

- 首次运行全量扫描，后续按 `updated_at` / `content_hash` 增量
- Markdown 文件路径固定为 `<markdown_root>/<note_id>/index.md`
- 图片文件路径固定为 `<markdown_root>/<note_id>/assets/<seq>.<ext>`
- 下载失败时保留远程 URL，不阻塞正文导出
- 远端消失的笔记只更新 `missing_from_remote=1`

**Step 4: 跑测试，确认通过**

Run:

```bash
uv sync
uv run python -m pytest tests/test_biji_sync.py -v
```

Expected: PASS

**Step 5: Commit**

```bash
git add pyproject.toml uv.lock core/biji_sync.py tests/test_biji_sync.py
git commit -m "feat: add biji sync service"
```

### Task 6: 增加命令行入口

**Files:**
- Create: `scripts/sync_biji.py`
- Create: `tests/test_biji_cli.py`

**Step 1: 写失败测试**

```python
from scripts.sync_biji import build_parser


def test_parser_defaults():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.page_size == 50
    assert args.full is False
```

**Step 2: 跑测试，确认失败**

Run: `uv run python -m pytest tests/test_biji_cli.py -v`

Expected: FAIL，提示 `scripts.sync_biji` 不存在

**Step 3: 写最小实现**

在 `scripts/sync_biji.py` 中实现：

- `build_parser()`
- `main()`

CLI 参数：

- `--base-dir`
- `--page-size`
- `--full`
- `--no-images`
- `-v/--verbose`

主流程：

- 从 `Storage` 读取本地 Biji 配置
- 校验 Bearer Token
- 组装 `BijiClient`、`BijiDB`、`BijiSyncService`
- 执行 `sync_once()`
- 在 stdout 打印 created / updated / skipped / failed 汇总

**Step 4: 跑测试，确认通过**

Run: `uv run python -m pytest tests/test_biji_cli.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add scripts/sync_biji.py tests/test_biji_cli.py
git commit -m "feat: add biji sync cli"
```

### Task 7: 更新文档并完成验证

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Step 1: 写 README 配置与使用说明**

补充以下内容：

- `config.json` 中的 `biji` 配置示例
- 首次同步命令
- 增量同步命令
- 本地输出目录说明
- Bearer Token 只保存在本地配置，不进仓库

**Step 2: 更新仓库约定**

在 `AGENTS.md` 中补充：

- `biji_notes.db`
- `biji_markdown/`
- `scripts/sync_biji.py`

**Step 3: 跑完整自动化测试**

Run:

```bash
uv run python -m pytest tests/test_storage_biji.py tests/test_biji_db.py tests/test_biji_client.py tests/test_biji_sync.py tests/test_biji_cli.py -v
```

Expected: PASS

**Step 4: 做手工冒烟**

Run:

```bash
uv run python scripts/sync_biji.py --page-size 20 -v
uv run python scripts/sync_biji.py --page-size 20 -v
```

Expected:

- 第一次运行打印 `created > 0`
- 第二次运行以 `updated` 或 `skipped` 为主
- `~/.investment-assistant/data/biji_markdown/` 下可打开离线 Markdown

**Step 5: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: add biji sync usage"
```
