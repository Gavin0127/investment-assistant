"""Parse Biji note content into normalized sections."""

from __future__ import annotations

ILLEGAL_TITLE_CHARS = '/\\:*?"<>|'
AI_SUMMARY_NOTE_TYPES = {"internal_record", "link"}


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
    original_content = (
        sections.get("original_content")
        or sections.get("native_content")
        or api_detail.get("ref_content")
        or ""
    ).strip()
    ai_summary_content = (sections.get("ai_summary_content") or "").strip()
    native_content = (sections.get("native_content") or "").strip()
    fallback_content = (api_detail.get("raw_content") or "").strip()

    if original_content and ai_summary_content:
        display_content = build_display_markdown_sections(
            {
                "content_mode": "ai_note",
                "original_content": original_content,
                "ai_summary_content": ai_summary_content,
            }
        )
        return {
            "content_mode": "ai_note",
            "original_content": original_content,
            "ai_summary_content": ai_summary_content,
            "display_content": display_content,
            "content_source": "mixed" if web_snapshot else "api_detail",
        }

    if _looks_like_ai_summary_note(api_detail):
        display_content = build_display_markdown_sections(
            {
                "content_mode": "ai_note",
                "original_content": original_content,
                "ai_summary_content": fallback_content or ai_summary_content,
            }
        )
        return {
            "content_mode": "ai_note",
            "original_content": original_content,
            "ai_summary_content": fallback_content or ai_summary_content,
            "display_content": display_content,
            "content_source": "mixed" if web_snapshot else "api_detail",
        }

    if native_content:
        return {
            "content_mode": "native_note",
            "original_content": "",
            "ai_summary_content": "",
            "display_content": native_content,
            "content_source": "web_page",
        }

    return {
        "content_mode": "unknown",
        "original_content": "",
        "ai_summary_content": "",
        "display_content": fallback_content,
        "content_source": "api_detail" if not web_snapshot else "mixed",
    }


def _looks_like_ai_summary_note(api_detail: dict) -> bool:
    note_type = str(api_detail.get("note_type") or "").strip().lower()
    if note_type in AI_SUMMARY_NOTE_TYPES:
        return True

    raw_content = (api_detail.get("raw_content") or "").strip()
    if raw_content.startswith("### 📑 智能总结") or raw_content.startswith("### **📑 智能总结"):
        return True

    return bool(api_detail.get("is_ai_generated")) or bool(api_detail.get("has_ai_processed"))


def build_display_markdown_sections(note: dict) -> str:
    if note.get("content_mode") == "ai_note":
        return (
            "## 原始内容\n\n"
            f"{(note.get('original_content') or '').strip()}\n\n"
            "## AI 总结（需验证可信度）\n\n"
            f"{(note.get('ai_summary_content') or '').strip()}\n"
        )
    if note.get("content_mode") == "unknown":
        return "## 内容摘录\n\n" + (note.get("display_content") or "").strip() + "\n"
    return "## 笔记正文\n\n" + (note.get("display_content") or "").strip() + "\n"
