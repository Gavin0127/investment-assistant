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
    slug = slugify_note_title("  字节/游戏:分析  ", note_id="1901", existing={"字节游戏分析"})
    assert slug == "字节游戏分析-1901"
