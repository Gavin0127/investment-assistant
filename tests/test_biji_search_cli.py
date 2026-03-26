import json
from pathlib import Path

from core.biji_search import BijiHybridSearchService
from scripts.search_biji_notes import main


def test_search_cli_returns_json_results(monkeypatch, capsys):
    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir or "/tmp/investment-assistant")

        def get_biji_retrieval_config(self):
            return {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "vector_db_dir": str(self.base_dir / "data" / "biji_vectors"),
                "chunk_size": 700,
                "chunk_overlap": 120,
                "top_k": 10,
            }

    class FakeSearchService:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def search(self, query, top_k=10):
            return {
                "query": query,
                "results": [
                    {
                        "note_id": "n1",
                        "title": "英伟达分析",
                        "section_type": "ai_summary_content",
                        "score": 0.91,
                        "text": "英伟达 token 经济与护城河",
                        "markdown_path": "/tmp/英伟达.md",
                    }
                    ],
                }

    monkeypatch.setattr("scripts.search_biji_notes.Storage", FakeStorage)
    monkeypatch.setattr("scripts.search_biji_notes.BijiDB", lambda *_args, **_kwargs: object())
    monkeypatch.setattr("scripts.search_biji_notes.OpenAIEmbedder", lambda **_kwargs: object())
    monkeypatch.setattr("scripts.search_biji_notes.BijiVectorStore", lambda **_kwargs: object())
    monkeypatch.setattr("scripts.search_biji_notes.BijiHybridSearchService", FakeSearchService)

    exit_code = main(["英伟达 护城河"])

    out = capsys.readouterr().out
    payload = json.loads(out)
    assert exit_code == 0
    assert payload["results"][0]["note_id"] == "n1"
    assert payload["results"][0]["markdown_path"] == "/tmp/英伟达.md"


def test_search_service_promotes_fts_hits_to_chunk_results():
    class FakeDB:
        def search_notes_fts(self, query):
            return [
                {
                    "note_id": "n1",
                    "title": "英伟达分析",
                    "display_content": "英伟达 token 经济与护城河",
                    "summary": "摘要",
                }
            ]

        def list_chunks(self, note_id):
            assert note_id == "n1"
            return [
                {
                    "chunk_id": "n1-0001",
                    "section_type": "ai_summary_content",
                    "text": "英伟达 token 经济与护城河",
                    "markdown_path": "/tmp/英伟达.md",
                }
            ]

    class FakeVectorStore:
        def search(self, query, top_k=10):
            return []

    service = BijiHybridSearchService(db=FakeDB(), vector_store=FakeVectorStore())

    result = service.search("英伟达", top_k=5)

    assert result["results"][0]["note_id"] == "n1"
    assert result["results"][0]["section_type"] == "ai_summary_content"
    assert result["results"][0]["markdown_path"] == "/tmp/英伟达.md"


def test_search_service_prefers_best_matching_fts_chunk_and_limits_one_chunk_per_note():
    class FakeDB:
        def search_notes_fts(self, query):
            return [
                {
                    "note_id": "n1",
                    "title": "英伟达分析",
                    "display_content": "摘要",
                }
            ]

        def list_chunks(self, note_id):
            return [
                {
                    "chunk_id": "n1-0001",
                    "section_type": "ai_summary_content",
                    "text": "只有英伟达，没有别的关键词",
                    "markdown_path": "/tmp/1.md",
                },
                {
                    "chunk_id": "n1-0002",
                    "section_type": "ai_summary_content",
                    "text": "英伟达 护城河 token 经济",
                    "markdown_path": "/tmp/2.md",
                },
            ]

    class FakeVectorStore:
        def search(self, query, top_k=10):
            return []

    service = BijiHybridSearchService(db=FakeDB(), vector_store=FakeVectorStore())

    result = service.search("英伟达 护城河", top_k=5)

    assert len(result["results"]) == 1
    assert result["results"][0]["markdown_path"] == "/tmp/2.md"
    assert result["results"][0]["score"] < 1.0


def test_search_service_degrades_to_fts_when_vector_search_fails():
    class FakeDB:
        def search_notes_fts(self, query):
            return [{"note_id": "n1", "title": "英伟达分析"}]

        def list_chunks(self, note_id):
            return [
                {
                    "chunk_id": "n1-0001",
                    "section_type": "ai_summary_content",
                    "text": "英伟达 护城河",
                    "markdown_path": "/tmp/英伟达.md",
                }
            ]

    class BrokenVectorStore:
        def search(self, query, top_k=10):
            raise RuntimeError("vector unavailable")

    service = BijiHybridSearchService(db=FakeDB(), vector_store=BrokenVectorStore())

    result = service.search("英伟达 护城河", top_k=5)

    assert result["results"][0]["note_id"] == "n1"
    assert result["results"][0]["markdown_path"] == "/tmp/英伟达.md"
