# Biji 正文导出纠偏 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 修正 Biji 笔记本地导出语义，支持把 AI 笔记拆成 `原始内容` 与 `AI 总结（需验证可信度）`，把原生笔记导成 `笔记正文`，并将导出目录改成基于标题命名，同时支持一键重建本地 Biji 数据。

**Architecture:** 保留现有 API 列表和详情作为同步主索引，在正文层新增页面快照提取与判型逻辑，把解析结果统一写入 SQLite 新字段并生成新的 Markdown 模板。同步入口增加显式 `--rebuild` 开关，用于清空旧的 Biji SQLite、Markdown 和 raw 快照后全量重建。

**Tech Stack:** Python 3.10+, sqlite3, pytest, requests, patchright, uv, markdownify

---

### Task 1: 固化正文判型与目录命名规则

**Files:**
- Create: `core/biji_content_parser.py`
- Create: `tests/test_biji_content_parser.py`

**Step 1: Write the failing test**

```python
from core.biji_content_parser import (
    build_display_markdown_sections,
    classify_note_content,
    slugify_note_title,
)


def test_classify_ai_note_with_original_and_summary():
    parsed = classify_note_content(
        api_detail={
            "note_id": "n1",
            "title": "AI 会议纪要",
            "raw_content": "整理后的摘要正文",
        },
        web_snapshot={
            "raw_sections": {
                "original_content": "逐字原文",
                "ai_summary_content": "整理后的摘要正文",
            }
        },
    )

    assert parsed["content_mode"] == "ai_note"
    assert parsed["original_content"] == "逐字原文"
    assert parsed["ai_summary_content"] == "整理后的摘要正文"


def test_classify_native_note_without_ai_summary():
    parsed = classify_note_content(
        api_detail={"note_id": "n2", "title": "手写笔记", "raw_content": "这是用户自己写的正文"},
        web_snapshot={"raw_sections": {"native_content": "这是用户自己写的正文"}},
    )

    assert parsed["content_mode"] == "native_note"
    assert parsed["original_content"] == ""
    assert parsed["ai_summary_content"] == ""
    assert parsed["display_content"] == "这是用户自己写的正文"


def test_build_display_markdown_sections_for_ai_note():
    markdown = build_display_markdown_sections(
        {
            "title": "AI 会议纪要",
            "content_mode": "ai_note",
            "original_content": "逐字原文",
            "ai_summary_content": "整理后的摘要正文",
        }
    )

    assert "## 原始内容" in markdown
    assert "## AI 总结（需验证可信度）" in markdown


def test_slugify_note_title_appends_note_id_for_duplicates():
    slug = slugify_note_title('  字节/游戏:分析  ', note_id='1901', existing={'字节游戏分析'})
    assert slug == '字节游戏分析-1901'
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_content_parser.py -v`

Expected: FAIL，提示 `core.biji_content_parser` 不存在

**Step 3: Write minimal implementation**

在 `core/biji_content_parser.py` 中实现：

```python
ILLEGAL_TITLE_CHARS = '/\\\\:*?"<>|'


def slugify_note_title(title: str, *, note_id: str, existing: set[str] | None = None) -> str:
    cleaned = title or ""
    for ch in ILLEGAL_TITLE_CHARS:
        cleaned = cleaned.replace(ch, "")
    cleaned = " ".join(cleaned.split()).strip()
    cleaned = cleaned[:80].strip()
    if not cleaned:
        cleaned = f"未命名笔记-{note_id}"
    existing = existing or set()
    if cleaned in existing:
        cleaned = f"{cleaned}-{note_id}"
    return cleaned


def classify_note_content(api_detail: dict, web_snapshot: dict | None) -> dict:
    sections = (web_snapshot or {}).get("raw_sections") or {}
    original = (sections.get("original_content") or "").strip()
    ai_summary = (sections.get("ai_summary_content") or "").strip()
    native = (sections.get("native_content") or api_detail.get("raw_content") or "").strip()
    if original or ai_summary:
        return {
            "content_mode": "ai_note",
            "original_content": original,
            "ai_summary_content": ai_summary or native,
            "display_content": build_display_markdown_sections(
                {
                    "title": api_detail.get("title") or "",
                    "content_mode": "ai_note",
                    "original_content": original,
                    "ai_summary_content": ai_summary or native,
                }
            ),
            "content_source": "mixed" if web_snapshot else "api_detail",
        }
    return {
        "content_mode": "native_note",
        "original_content": "",
        "ai_summary_content": "",
        "display_content": native,
        "content_source": "web_page" if web_snapshot else "api_detail",
    }


def build_display_markdown_sections(note: dict) -> str:
    if note.get("content_mode") == "ai_note":
        return (
            "## 原始内容\\n\\n"
            f"{(note.get('original_content') or '').strip()}\\n\\n"
            "## AI 总结（需验证可信度）\\n\\n"
            f"{(note.get('ai_summary_content') or '').strip()}\\n"
        )
    return "## 笔记正文\\n\\n" + (note.get("display_content") or "").strip() + "\\n"
```

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_content_parser.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_content_parser.py tests/test_biji_content_parser.py
git commit -m "feat: add biji content parser"
```

### Task 2: 扩展 SQLite schema 保存语义字段与导出目录名

**Files:**
- Modify: `core/biji_db.py`
- Modify: `tests/test_biji_db.py`

**Step 1: Write the failing test**

```python
def test_upsert_note_persists_content_mode_and_export_dir_name(tmp_path):
    db = BijiDB(str(tmp_path / "biji.db"))
    db.upsert_note(
        _make_note(
            "n1",
            content_mode="ai_note",
            original_content="逐字原文",
            ai_summary_content="AI 摘要",
            display_content="## 原始内容\\n\\n逐字原文",
            content_source="mixed",
            export_dir_name="字节游戏分析",
        )
    )

    note = db.get_note("n1")

    assert note["content_mode"] == "ai_note"
    assert note["original_content"] == "逐字原文"
    assert note["ai_summary_content"] == "AI 摘要"
    assert note["export_dir_name"] == "字节游戏分析"
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_db.py::test_upsert_note_persists_content_mode_and_export_dir_name -v`

Expected: FAIL，提示 `notes` 表缺少新增字段，或返回结果中没有对应键

**Step 3: Write minimal implementation**

修改 `core/biji_db.py`：

- 在 `notes` 表增加字段
  - `content_mode TEXT`
  - `original_content TEXT`
  - `ai_summary_content TEXT`
  - `display_content TEXT`
  - `content_source TEXT`
  - `export_dir_name TEXT`
- 为已存在数据库增加一次 schema 兼容逻辑
  - 在 `_init_schema()` 中检查列是否存在
  - 缺列则执行 `ALTER TABLE`
- 在 `upsert_note()` 的 `INSERT ... ON CONFLICT` 中补齐新字段

需要把测试辅助方法 `_make_note()` 也扩成默认包含这些键：

```python
{
    "content_mode": "unknown",
    "original_content": "",
    "ai_summary_content": "",
    "display_content": "",
    "content_source": "api_detail",
    "export_dir_name": f"title-{note_id}",
}
```

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_db.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_db.py tests/test_biji_db.py
git commit -m "feat: persist biji content semantics"
```

### Task 3: 给浏览器客户端增加页面快照能力

**Files:**
- Modify: `core/biji_browser_client.py`
- Modify: `tests/test_biji_browser_client.py`

**Step 1: Write the failing test**

```python
def test_get_note_page_snapshot_returns_html_and_visible_text(monkeypatch, tmp_path):
    from core.biji_browser_client import BijiBrowserClient

    client = BijiBrowserClient(
        api_base="https://notes-api.biji.com",
        profile_dir=str(tmp_path / "profile"),
    )

    class FakeLocator:
        def inner_text(self, timeout=None):
            return "正文\\n原始内容\\nAI 总结"

    class FakePage:
        def __init__(self):
            self.visited = []

        def goto(self, url, wait_until=None, timeout=None):
            self.visited.append(url)

        def wait_for_timeout(self, ms):
            return None

        def content(self):
            return "<html><body><h2>原始内容</h2><div>逐字原文</div></body></html>"

        def locator(self, selector):
            assert selector == "body"
            return FakeLocator()

    fake_page = FakePage()
    monkeypatch.setattr(client, "_ensure_browser", lambda: fake_page)
    monkeypatch.setattr(client, "_ensure_page_ready", lambda: fake_page)

    snapshot = client.get_note_page_snapshot("1901")

    assert snapshot["note_url"].endswith("/1901/web")
    assert "原始内容" in snapshot["html"]
    assert "AI 总结" in snapshot["text"]
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_browser_client.py::test_get_note_page_snapshot_returns_html_and_visible_text -v`

Expected: FAIL，提示 `BijiBrowserClient` 缺少 `get_note_page_snapshot`

**Step 3: Write minimal implementation**

在 `core/biji_browser_client.py` 增加：

```python
def get_note_page_snapshot(self, note_id: str) -> dict[str, str]:
    page = self._ensure_browser()
    note_url = f"https://www.biji.com/note/{note_id}/web"
    page.goto(note_url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
    page.wait_for_timeout(1500)
    return {
        "note_url": note_url,
        "html": page.content(),
        "text": page.locator("body").inner_text(timeout=self.timeout * 1000),
    }
```

如果 `/web` 回到营销登录页，不要抛异常；由上层判型逻辑决定是否回退。

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_browser_client.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_browser_client.py tests/test_biji_browser_client.py
git commit -m "feat: add biji note page snapshot"
```

### Task 4: 集成正文判型、标题目录导出和 raw 快照分层

**Files:**
- Modify: `core/biji_sync.py`
- Modify: `tests/test_biji_sync.py`

**Step 1: Write the failing test**

```python
def test_sync_exports_ai_note_with_title_directory_and_dual_sections(tmp_path):
    from core.biji_sync import BijiSyncService

    class PageAwareClient(FakeClient):
        def get_note_page_snapshot(self, note_id: str):
            return {
                "note_url": f"https://www.biji.com/note/{note_id}/web",
                "html": "<h2>原始内容</h2><div>逐字原文</div><h2>AI 总结</h2><div>整理摘要</div>",
                "text": "原始内容\\n逐字原文\\nAI 总结\\n整理摘要",
            }

    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=PageAwareClient(),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    result = service.sync_once()

    assert result["created"] == 1
    note = db.get_note("n1")
    assert note["content_mode"] == "ai_note"
    assert note["original_content"] == "逐字原文"
    assert note["ai_summary_content"] == "整理摘要"
    assert note["export_dir_name"] == "第一篇"
    markdown_path = tmp_path / "biji_markdown" / "第一篇" / "index.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "## 原始内容" in markdown
    assert "## AI 总结（需验证可信度）" in markdown
    assert not (tmp_path / "biji_markdown" / "n1").exists()
```

再加一条原生笔记测试：

```python
def test_sync_exports_native_note_without_ai_heading(tmp_path):
    from core.biji_sync import BijiSyncService

    class NativeClient(FakeClient):
        def get_note_page_snapshot(self, note_id: str):
            return {
                "note_url": f"https://www.biji.com/note/{note_id}/web",
                "html": "<article><p>这是原生正文</p></article>",
                "text": "这是原生正文",
            }

    db = BijiDB(str(tmp_path / "biji.db"))
    service = BijiSyncService(
        client=NativeClient(detail_overrides={"raw_content": "这是原生正文"}),
        db=db,
        markdown_root=str(tmp_path / "biji_markdown"),
        raw_root=str(tmp_path / "biji_raw"),
        page_size=10,
    )

    service.sync_once()

    markdown = (tmp_path / "biji_markdown" / "第一篇" / "index.md").read_text(encoding="utf-8")
    assert "## 笔记正文" in markdown
    assert "AI 总结（需验证可信度）" not in markdown
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_sync.py -v`

Expected: FAIL，原因包括：

- 导出目录仍是 `note_id`
- Markdown 模板缺少新章节
- `sync_once()` 没有写入新增语义字段

**Step 3: Write minimal implementation**

在 `core/biji_sync.py` 中做以下改动：

- 引入 `core.biji_content_parser`
- 在每条笔记同步时：
  - 调用 `client.get_note_page_snapshot(note_id)`，若客户端无此方法则返回 `None`
  - 调用 `classify_note_content(detail, web_snapshot)`
  - 生成 `content_mode`、`original_content`、`ai_summary_content`、`display_content`、`content_source`
- 调整 `_export_note_markdown()`：
  - 目录改为 `export_dir_name`
  - frontmatter 增加 `content_mode`
  - 正文通过 `build_display_markdown_sections()` 输出
- 调整 `_write_raw_snapshot()`：
  - 统一写入 `api_detail`、`web_snapshot`、`normalized_note`
- 新增目录迁移/清理逻辑：
  - 若旧目录名与新目录名不一致，先删除旧导出目录再写新目录

核心写法可以先保持最小：

```python
web_snapshot = None
if hasattr(self.client, "get_note_page_snapshot"):
    web_snapshot = self.client.get_note_page_snapshot(note_id)

parsed = classify_note_content(detail, web_snapshot)
export_dir_name = slugify_note_title(
    detail.get("title") or summary.get("title") or "",
    note_id=note_id,
    existing=self._used_export_dirs,
)
```

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_sync.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add core/biji_sync.py tests/test_biji_sync.py core/biji_content_parser.py
git commit -m "feat: export biji notes with semantic sections"
```

### Task 5: 给 CLI 增加显式重建开关并验证清理行为

**Files:**
- Modify: `scripts/sync_biji.py`
- Modify: `tests/test_biji_cli.py`

**Step 1: Write the failing test**

```python
def test_main_rebuild_removes_old_biji_data_before_sync(monkeypatch, tmp_path, capsys):
    data_dir = tmp_path / "data"
    (data_dir / "biji_markdown" / "旧目录").mkdir(parents=True)
    (data_dir / "biji_markdown" / "旧目录" / "index.md").write_text("old", encoding="utf-8")
    (data_dir / "biji_raw").mkdir(parents=True)
    (data_dir / "biji_raw" / "n1.json").write_text("{}", encoding="utf-8")
    (data_dir / "biji_notes.db").write_text("not-a-real-db", encoding="utf-8")

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_config(self):
            return {
                "auth_mode": "browser_session",
                "api_base": "https://notes-api.biji.com",
                "browser_profile_dir": str(self.base_dir / "data" / "biji_browser"),
                "page_size": 50,
                "download_images": True,
            }

    class FakeBrowserClient:
        def __init__(self, *args, **kwargs):
            self.download_asset = lambda *_args, **_kwargs: None

        def close(self):
            return None

    class FakeDB:
        def __init__(self, db_path):
            assert not Path(db_path).exists()

    class FakeSyncService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def sync_once(self):
            return {"created": 1, "updated": 0, "skipped": 0, "failed": 0}

    monkeypatch.setattr("scripts.sync_biji.Storage", FakeStorage)
    monkeypatch.setattr("scripts.sync_biji.BijiBrowserClient", FakeBrowserClient)
    monkeypatch.setattr("scripts.sync_biji.BijiDB", FakeDB)
    monkeypatch.setattr("scripts.sync_biji.BijiSyncService", FakeSyncService)

    exit_code = main(["--base-dir", str(tmp_path), "--rebuild"])

    assert exit_code == 0
    assert not (data_dir / "biji_markdown" / "旧目录").exists()
    assert not (data_dir / "biji_raw" / "n1.json").exists()
```

**Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/test_biji_cli.py::test_main_rebuild_removes_old_biji_data_before_sync -v`

Expected: FAIL，提示解析器没有 `--rebuild` 或清理逻辑未执行

**Step 3: Write minimal implementation**

修改 `scripts/sync_biji.py`：

- 在 `build_parser()` 增加：

```python
parser.add_argument("--rebuild", action="store_true", help="清空本地 Biji 数据后重建")
```

- 新增辅助函数：

```python
from pathlib import Path
import shutil


def rebuild_biji_data(data_dir: Path) -> None:
    for path in [
        data_dir / "biji_notes.db",
        data_dir / "biji_markdown",
        data_dir / "biji_raw",
    ]:
        if path.is_dir():
            shutil.rmtree(path)
        elif path.exists():
            path.unlink()
```

- 在构造 `BijiDB` 之前执行：

```python
if args.rebuild:
    rebuild_biji_data(data_dir)
```

不要清理 `biji_browser/`。

**Step 4: Run test to verify it passes**

Run: `uv run python -m pytest tests/test_biji_cli.py -v`

Expected: PASS

**Step 5: Commit**

```bash
git add scripts/sync_biji.py tests/test_biji_cli.py
git commit -m "feat: add biji rebuild option"
```

### Task 6: 全量验证与真实重建

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`

**Step 1: Write the failing test**

这一步不新增自动化测试，先补文档缺口。把 README 与 AGENTS 中所有基于旧格式的描述替换为新语义：

- `biji_markdown/<note_id>/index.md` 改为 `biji_markdown/<title-or-title-note_id>/index.md`
- 说明 AI 笔记与原生笔记的不同章节结构
- 说明 `--rebuild` 会清空 `biji_notes.db`、`biji_markdown/`、`biji_raw/`

**Step 2: Run test to verify it fails**

Run: `rg -n \"biji_markdown/<note_id>|AI 总结|笔记正文|--rebuild\" README.md AGENTS.md`

Expected:

- 仍能搜到旧的 `<note_id>` 路径描述
- 搜不到新的 `--rebuild` 用法说明

**Step 3: Write minimal implementation**

修改 `README.md` 和 `AGENTS.md`：

- 更新数据目录示例
- 更新同步命令示例
- 增加以下真实重建命令：

```bash
uv run python scripts/sync_biji.py --rebuild -v
uv run python scripts/sync_biji.py -v
```

- 在文档中明确：
  - AI 笔记导出章节
  - 原生笔记导出章节
  - 站外链接只记录，不穿透抓正文

**Step 4: Run test to verify it passes**

Run:

```bash
uv run python -m pytest
uv run python scripts/sync_biji.py --rebuild -v
uv run python scripts/sync_biji.py -v
```

Expected:

- 测试全绿
- 第一轮真实重建成功
- 第二轮增量结果为 `created=0 updated=0 skipped=N failed=0`

额外人工抽查 2 条笔记：

- 1 条 AI 笔记，Markdown 中同时包含 `原始内容` 与 `AI 总结（需验证可信度）`
- 1 条原生笔记，Markdown 中只包含 `笔记正文`

**Step 5: Commit**

```bash
git add README.md AGENTS.md
git commit -m "docs: describe biji semantic export"
```
