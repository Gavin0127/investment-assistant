import sys
from types import SimpleNamespace

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


def test_upsert_chunks_replaces_existing_note_rows(tmp_path):
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
                "title": "第一版",
                "section_type": "ai_summary_content",
                "text": "旧内容",
                "markdown_path": "/tmp/old.md",
            }
        ]
    )
    store.upsert_chunks(
        [
            {
                "chunk_id": "n1-0002",
                "note_id": "n1",
                "title": "第二版",
                "section_type": "ai_summary_content",
                "text": "新内容",
                "markdown_path": "/tmp/new.md",
            }
        ]
    )

    hits = store.search("新内容", top_k=5)

    assert [item["chunk_id"] for item in hits] == ["n1-0002"]


def test_lancedb_backend_creates_table_and_returns_descending_scores(tmp_path, monkeypatch):
    class FakeSearchResult:
        def limit(self, top_k):
            self.top_k = top_k
            return self

        def to_list(self):
            return [
                {
                    "chunk_id": "n1-0001",
                    "note_id": "n1",
                    "title": "英伟达分析",
                    "section_type": "ai_summary_content",
                    "text": "英伟达 token 经济与护城河",
                    "markdown_path": "/tmp/英伟达.md",
                    "_distance": 0.25,
                }
            ]

    class FakeTable:
        def __init__(self):
            self.deleted = []
            self.added = []

        def delete(self, expr):
            self.deleted.append(expr)

        def add(self, rows):
            self.added.extend(rows)

        def search(self, query_vector):
            return FakeSearchResult()

    class FakeDB:
        def __init__(self):
            self.table = None
            self.created = []

        def open_table(self, name):
            raise RuntimeError("missing table")

        def create_table(self, name, data, mode="overwrite"):
            self.created.append((name, data, mode))
            self.table = FakeTable()
            return self.table

    fake_db = FakeDB()
    fake_lancedb = SimpleNamespace(connect=lambda _path: fake_db)
    monkeypatch.setitem(sys.modules, "lancedb", fake_lancedb)

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

    assert fake_db.created[0][0] == "biji_chunks"
    assert fake_db.table.deleted == ["note_id = 'n1'"]
    assert 0.0 < hits[0]["score"] <= 1.0
