from core.biji_chunking import chunk_note, estimate_tokens, split_text_with_overlap


def test_chunk_ai_note_splits_original_and_summary_sections():
    chunks = chunk_note(
        {
            "note_id": "n1",
            "title": "英伟达分析",
            "content_mode": "ai_note",
            "original_content": "第一段原始内容\n\n第二段原始内容",
            "ai_summary_content": "### 结论\n\nAI总结正文",
            "export_dir_name": "英伟达分析",
        },
        chunk_size=50,
        chunk_overlap=10,
        markdown_root="/tmp/biji_markdown",
    )

    section_types = [item["section_type"] for item in chunks]

    assert "original_content" in section_types
    assert "ai_summary_content" in section_types
    assert all(item["note_id"] == "n1" for item in chunks)
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

    assert len(chunks) == 1
    assert chunks[0]["section_type"] == "content_excerpt"
    assert chunks[0]["text"] == "一段未知正文"
    assert chunks[0]["markdown_path"].endswith("/未知笔记/index.md")


def test_split_text_with_overlap_preserves_progress():
    chunks = split_text_with_overlap("abcdefghijklmnopqrstuvwxyz", chunk_size=10, chunk_overlap=3)

    assert chunks == [
        (0, 10, "abcdefghij"),
        (7, 17, "hijklmnopq"),
        (14, 24, "opqrstuvwx"),
        (21, 26, "vwxyz"),
    ]


def test_split_text_with_overlap_clamps_invalid_overlap():
    chunks = split_text_with_overlap("abcdefghij", chunk_size=4, chunk_overlap=10)

    assert chunks == [
        (0, 4, "abcd"),
        (1, 5, "bcde"),
        (2, 6, "cdef"),
        (3, 7, "defg"),
        (4, 8, "efgh"),
        (5, 9, "fghi"),
        (6, 10, "ghij"),
    ]


def test_chunk_note_rejects_unsafe_export_dir_name():
    chunks = chunk_note(
        {
            "note_id": "n3",
            "content_mode": "unknown",
            "display_content": "正文",
            "export_dir_name": "../escape",
        },
        chunk_size=50,
        chunk_overlap=10,
        markdown_root="/tmp/biji_markdown",
    )

    assert chunks[0]["markdown_path"].endswith("/n3/index.md")


def test_estimate_tokens_returns_positive_count():
    assert estimate_tokens("英伟达 token economy") > 0
