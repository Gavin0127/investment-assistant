"""Chunk Biji notes into retrieval-friendly sections."""

from __future__ import annotations

import hashlib
from pathlib import Path


def estimate_tokens(text: str) -> int:
    """Approximate tokens from character length for mixed CJK/ASCII text."""
    text = (text or "").strip()
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _safe_dir_component(value: str, fallback: str) -> str:
    text = (value or "").strip()
    if not text:
        return fallback

    path = Path(text)
    parts = [part for part in path.parts if part not in ("", ".")]
    if path.is_absolute() or len(parts) != 1 or parts[0] == "..":
        return fallback
    return parts[0]


def split_text_with_overlap(text: str, chunk_size: int, chunk_overlap: int) -> list[tuple[int, int, str]]:
    text = (text or "").strip()
    if not text:
        return []

    chunk_size = max(1, int(chunk_size))
    chunk_overlap = max(0, min(int(chunk_overlap), chunk_size - 1))
    step = max(1, chunk_size - chunk_overlap)

    chunks: list[tuple[int, int, str]] = []
    start = 0
    text_length = len(text)

    while start < text_length:
        end = min(start + chunk_size, text_length)
        chunks.append((start, end, text[start:end]))
        if end >= text_length:
            break
        start += step

    return chunks


def chunk_note(note: dict, chunk_size: int, chunk_overlap: int, markdown_root: str) -> list[dict]:
    note_id = str(note.get("note_id") or "")
    safe_note_id = _safe_dir_component(note_id, "note")
    export_dir_name = _safe_dir_component(
        str(note.get("export_dir_name") or ""),
        safe_note_id,
    )
    markdown_path = str(Path(markdown_root) / export_dir_name / "index.md")

    sections: list[tuple[str, str]] = []
    content_mode = str(note.get("content_mode") or "").strip()
    if content_mode == "ai_note":
        sections.extend(
            [
                ("original_content", (note.get("original_content") or "").strip()),
                ("ai_summary_content", (note.get("ai_summary_content") or "").strip()),
            ]
        )
    elif content_mode == "native_note":
        sections.append(("native_body", (note.get("display_content") or "").strip()))
    else:
        sections.append(("content_excerpt", (note.get("display_content") or "").strip()))

    results: list[dict] = []
    chunk_index = 0
    for section_type, section_text in sections:
        if not section_text:
            continue
        for char_start, char_end, chunk_text in split_text_with_overlap(
            section_text,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        ):
            # Offsets are section-local, not file-global. Retrieval consumers use
            # markdown_path + section_type for traceability and treat these as intra-section spans.
            chunk_index += 1
            results.append(
                {
                    "chunk_id": f"{note_id}-{chunk_index:04d}",
                    "note_id": note_id,
                    "chunk_index": chunk_index,
                    "section_type": section_type,
                    "text": chunk_text,
                    "token_estimate": estimate_tokens(chunk_text),
                    "char_start": char_start,
                    "char_end": char_end,
                    "content_hash": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                    "markdown_path": markdown_path,
                }
            )

    return results
