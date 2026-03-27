"""Local vector sidecar for Biji retrieval."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from openai import OpenAI


class OpenAIEmbedder:
    def __init__(
        self,
        model: str = "text-embedding-3-large",
        api_key: str | None = None,
        base_url: str | None = None,
        client: OpenAI | None = None,
    ):
        self.model = model
        if client is not None:
            self.client = client
        else:
            client_kwargs = {"api_key": api_key}
            if base_url:
                client_kwargs["base_url"] = base_url
            self.client = OpenAI(**client_kwargs)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        items = [str(text or "") for text in texts]
        if not items:
            return []
        response = self.client.embeddings.create(model=self.model, input=items)
        return [list(row.embedding) for row in response.data]


class BijiVectorStore:
    def __init__(
        self,
        db_dir: str,
        table_name: str = "biji_chunks",
        *,
        embedder: Any | None = None,
    ):
        self.db_dir = Path(db_dir)
        self.table_name = table_name
        self.embedder = embedder or OpenAIEmbedder()
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._table = None
        self._table_path = self.db_dir / f"{self.table_name}.json"
        try:
            import lancedb  # type: ignore

            self._db = lancedb.connect(str(self.db_dir))
            self._backend = "lancedb"
        except Exception:
            self._db = None
            self._backend = "json"

    def _open_table(self):
        if self._backend != "lancedb":
            return None
        if self._table is not None:
            return self._table
        try:
            self._table = self._db.open_table(self.table_name)
        except Exception:
            self._table = None
        return self._table

    def _ensure_table(self, rows: list[dict]):
        if self._backend != "lancedb":
            return None
        table = self._open_table()
        if table is not None:
            return table
        self._table = self._db.create_table(self.table_name, data=rows, mode="overwrite")
        return self._table

    def _load_rows(self) -> list[dict]:
        if not self._table_path.exists():
            return []
        return json.loads(self._table_path.read_text(encoding="utf-8"))

    def _save_rows(self, rows: list[dict]) -> None:
        self._table_path.write_text(
            json.dumps(rows, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _dot(left: list[float], right: list[float]) -> float:
        return sum(a * b for a, b in zip(left, right))

    @classmethod
    def _cosine_similarity(cls, left: list[float], right: list[float]) -> float:
        left_norm = math.sqrt(cls._dot(left, left))
        right_norm = math.sqrt(cls._dot(right, right))
        if left_norm == 0 or right_norm == 0:
            return 0.0
        return cls._dot(left, right) / (left_norm * right_norm)

    @staticmethod
    def _quote_literal(value: str) -> str:
        return "'" + str(value).replace("'", "''") + "'"

    def upsert_chunks(self, chunks: list[dict]) -> None:
        if not chunks:
            return

        vectors = self.embedder.embed_texts([chunk["text"] for chunk in chunks])
        rows = []
        for chunk, vector in zip(chunks, vectors):
            rows.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "note_id": chunk["note_id"],
                    "title": chunk.get("title") or "",
                    "section_type": chunk["section_type"],
                    "text": chunk["text"],
                    "markdown_path": chunk["markdown_path"],
                    "vector": vector,
                }
            )

        note_ids = {row["note_id"] for row in rows}
        table = self._open_table()
        if table is not None:
            for note_id in note_ids:
                table.delete(f"note_id = {self._quote_literal(note_id)}")
            table.add(rows)
            return

        if self._backend == "lancedb":
            table = self._ensure_table(rows)
            for note_id in note_ids:
                table.delete(f"note_id = {self._quote_literal(note_id)}")
            table.add(rows)
            return

        existing = {
            row["chunk_id"]: row
            for row in self._load_rows()
        }
        for chunk_id, row in list(existing.items()):
            if row["note_id"] in note_ids:
                del existing[chunk_id]
        for row in rows:
            existing[row["chunk_id"]] = row
        self._save_rows(list(existing.values()))

    def delete_chunks_for_note(self, note_id: str) -> None:
        table = self._open_table()
        if table is not None:
            table.delete(f"note_id = {self._quote_literal(note_id)}")
            return

        rows = [row for row in self._load_rows() if row["note_id"] != note_id]
        self._save_rows(rows)

    def delete_chunks_not_in(self, note_ids: set[str]) -> None:
        table = self._open_table()
        if table is not None:
            rows = table.to_list()
            stale_note_ids = {
                str(row.get("note_id") or "")
                for row in rows
                if str(row.get("note_id") or "") and str(row.get("note_id") or "") not in note_ids
            }
            for note_id in stale_note_ids:
                table.delete(f"note_id = {self._quote_literal(note_id)}")
            return

        rows = [row for row in self._load_rows() if row["note_id"] in note_ids]
        self._save_rows(rows)

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        query_vector = self.embedder.embed_texts([query])[0]
        table = self._open_table()
        if table is not None:
            rows = table.search(query_vector).limit(top_k).to_list()
            return [
                {
                    "chunk_id": row["chunk_id"],
                    "note_id": row["note_id"],
                    "title": row["title"],
                    "section_type": row["section_type"],
                    "text": row["text"],
                    "markdown_path": row["markdown_path"],
                    "score": 1.0 / (1.0 + float(row.get("_distance") or 0.0)),
                }
                for row in rows
            ]

        rows = self._load_rows()
        scored = []
        for row in rows:
            vector = row.get("vector") or []
            if not vector:
                continue
            scored.append(
                {
                    "chunk_id": row["chunk_id"],
                    "note_id": row["note_id"],
                    "title": row["title"],
                    "section_type": row["section_type"],
                    "text": row["text"],
                    "markdown_path": row["markdown_path"],
                    "score": self._cosine_similarity(query_vector, vector),
                }
            )
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:top_k]
