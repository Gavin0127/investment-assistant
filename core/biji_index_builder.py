"""Index builder for local Biji retrieval."""

from __future__ import annotations

from pathlib import Path

from core.biji_chunking import chunk_note
from core.biji_db import BijiDB
from core.biji_vector_store import BijiVectorStore


class BijiIndexBuilder:
    """Build and refresh local Biji chunk + vector indexes."""

    def __init__(
        self,
        *,
        db: BijiDB,
        vector_store: BijiVectorStore,
        markdown_root: str,
        vector_db_dir: str,
        chunk_size: int,
        chunk_overlap: int,
    ):
        self.db = db
        self.vector_store = vector_store
        self.markdown_root = str(markdown_root)
        self.vector_db_dir = Path(vector_db_dir)
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._vector_enabled = True

    def rebuild(self, full_rebuild: bool = False) -> dict[str, int]:
        notes = self.db.list_notes()
        notes_indexed = 0
        chunks_indexed = 0

        for note in notes:
            note_id = str(note.get("note_id") or "")
            chunks = chunk_note(
                note,
                chunk_size=self.chunk_size,
                chunk_overlap=self.chunk_overlap,
                markdown_root=self.markdown_root,
            )
            enriched_chunks = [
                {
                    **chunk,
                    "title": note.get("title") or "",
                }
                for chunk in chunks
            ]
            self.db.replace_chunks_for_note(note_id, enriched_chunks)
            if self._vector_enabled:
                try:
                    self.vector_store.delete_chunks_for_note(note_id)
                    if enriched_chunks:
                        self.vector_store.upsert_chunks(enriched_chunks)
                except Exception:
                    self._vector_enabled = False
            notes_indexed += 1
            chunks_indexed += len(enriched_chunks)

        return {
            "notes_indexed": notes_indexed,
            "chunks_indexed": chunks_indexed,
        }
