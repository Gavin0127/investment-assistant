from pathlib import Path

from scripts.index_biji_notes import main


def test_index_cli_rebuilds_chunks_and_vectors(monkeypatch, tmp_path, capsys):
    state = {}

    class FakeStorage:
        def __init__(self, base_dir=None):
            self.base_dir = Path(base_dir)

        def get_biji_retrieval_config(self):
            return {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "embedding_api_key": "test-key",
                "embedding_base_url": None,
                "vector_db_dir": str(self.base_dir / "data" / "biji_vectors"),
                "chunk_size": 700,
                "chunk_overlap": 120,
                "top_k": 10,
            }

        def get_api_key(self):
            return "test-key"

        def get_llm_base_url(self):
            return None

    class FakeIndexer:
        def __init__(self, **kwargs):
            state["kwargs"] = kwargs

        def rebuild(self, full_rebuild=False):
            state["full_rebuild"] = full_rebuild
            return {"notes_indexed": 5, "chunks_indexed": 12}

    class FakeEmbedder:
        def __init__(self, model, api_key=None, base_url=None):
            state["embedding_model"] = model
            state["embedding_base_url"] = base_url

    monkeypatch.setattr("scripts.index_biji_notes.Storage", FakeStorage)
    monkeypatch.setattr("scripts.index_biji_notes.OpenAIEmbedder", FakeEmbedder)
    monkeypatch.setattr("scripts.index_biji_notes.BijiVectorStore", lambda **kwargs: kwargs)
    monkeypatch.setattr("scripts.index_biji_notes.BijiIndexBuilder", FakeIndexer)

    exit_code = main(["--base-dir", str(tmp_path), "--rebuild"])

    out = capsys.readouterr().out
    assert exit_code == 0
    assert state["kwargs"]["vector_db_dir"] == str(tmp_path / "data" / "biji_vectors")
    assert state["embedding_model"] == "text-embedding-3-large"
    assert state["embedding_base_url"] is None
    assert state["full_rebuild"] is True
    assert "notes_indexed=5" in out
    assert "chunks_indexed=12" in out


def test_index_builder_deletes_old_vectors_when_note_has_no_chunks(tmp_path):
    from core.biji_index_builder import BijiIndexBuilder

    state = {"deleted": [], "upserted": []}

    class FakeDB:
        def list_notes(self):
            return [{"note_id": "n1", "content_mode": "unknown", "display_content": "", "export_dir_name": "n1"}]

        def replace_chunks_for_note(self, note_id, chunks):
            state["replaced"] = (note_id, chunks)

    class FakeVectorStore:
        def delete_chunks_for_note(self, note_id):
            state["deleted"].append(note_id)

        def upsert_chunks(self, chunks):
            state["upserted"].append(chunks)

    builder = BijiIndexBuilder(
        db=FakeDB(),
        vector_store=FakeVectorStore(),
        markdown_root=str(tmp_path / "biji_markdown"),
        vector_db_dir=str(tmp_path / "biji_vectors"),
        chunk_size=100,
        chunk_overlap=20,
    )

    result = builder.rebuild(full_rebuild=False)

    assert result == {"notes_indexed": 1, "chunks_indexed": 0}
    assert state["deleted"] == ["n1"]
    assert state["upserted"] == []
