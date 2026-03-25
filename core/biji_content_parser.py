"""Parse Biji note content into normalized sections."""

from __future__ import annotations

ILLEGAL_TITLE_CHARS = '/\\:*?"<>|'


def slugify_note_title(
    title: str,
    *,
    note_id: str,
    existing: set[str] | None = None,
) -> str:
    cleaned = title or ""
    for ch in ILLEGAL_TITLE_CHARS:
        cleaned = cleaned.replace(ch, "")
    cleaned = " ".join(cleaned.split()).strip()
    cleaned = cleaned[:80].strip()
    if not cleaned:
        cleaned = f"未命名笔记-{note_id}"
    if cleaned in (existing or set()):
        cleaned = f"{cleaned}-{note_id}"
    return cleaned


def classify_note_content(api_detail: dict, web_snapshot: dict | None) -> dict:
    sections = (web_snapshot or {}).get("raw_sections") or {}
    original_content = (sections.get("original_content") or "").strip()
    ai_summary_content = (sections.get("ai_summary_content") or "").strip()
    native_content = (sections.get("native_content") or api_detail.get("raw_content") or "").strip()

    if original_content or ai_summary_content:
        display_content = build_display_markdown_sections(
            {
                "content_mode": "ai_note",
                "original_content": original_content,
                "ai_summary_content": ai_summary_content or native_content,
            }
        )
        return {
            "content_mode": "ai_note",
            "original_content": original_content,
            "ai_summary_content": ai_summary_content or native_content,
            "display_content": display_content,
            "content_source": "mixed" if web_snapshot else "api_detail",
        }

    return {
        "content_mode": "native_note",
        "original_content": "",
        "ai_summary_content": "",
        "display_content": native_content,
        "content_source": "web_page" if web_snapshot else "api_detail",
    }


def build_display_markdown_sections(note: dict) -> str:
    if note.get("content_mode") == "ai_note":
        return (
            "## 原始内容\n\n"
            f"{(note.get('original_content') or '').strip()}\n\n"
            "## AI 总结（需验证可信度）\n\n"
            f"{(note.get('ai_summary_content') or '').strip()}\n"
        )
    return "## 笔记正文\n\n" + (note.get("display_content") or "").strip() + "\n"
