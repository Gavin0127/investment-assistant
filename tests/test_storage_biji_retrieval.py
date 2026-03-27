from core.storage import Storage


def test_get_biji_retrieval_config_reads_local_settings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config(
        {
            "biji_retrieval": {
                "embedding_provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "vector_db_dir": "~/custom-biji-vectors",
                "chunk_size": 700,
                "chunk_overlap": 120,
                "top_k": 12,
            }
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_provider"] == "openai"
    assert cfg["embedding_model"] == "text-embedding-3-large"
    assert cfg["vector_db_dir"] == "~/custom-biji-vectors"
    assert cfg["chunk_size"] == 700
    assert cfg["chunk_overlap"] == 120
    assert cfg["top_k"] == 12


def test_get_biji_retrieval_config_has_safe_defaults(tmp_path):
    storage = Storage(base_dir=str(tmp_path))

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_provider"] == "openai"
    assert cfg["embedding_model"] == "text-embedding-3-large"
    assert cfg["embedding_api_key"] == ""
    assert cfg["embedding_base_url"] == ""
    assert cfg["vector_db_dir"] == str(tmp_path / "data" / "biji_vectors")
    assert cfg["chunk_size"] == 700
    assert cfg["chunk_overlap"] == 120
    assert cfg["top_k"] == 10


def test_get_biji_retrieval_config_prefers_openai_key_even_when_llm_provider_is_gemini(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config(
        {
            "llm_provider": "gemini",
            "gemini_api_key": "gemini-key",
            "openai_api_key": "openai-key",
            "llm_base_url": "https://right.codes/codex/v1",
            "biji_retrieval": {
                "embedding_provider": "openai",
            },
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_api_key"] == "openai-key"
    assert cfg["embedding_base_url"] == "https://right.codes/codex/v1"


def test_get_biji_retrieval_config_falls_back_for_blank_strings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config(
        {
            "biji_retrieval": {
                "embedding_provider": "   ",
                "embedding_model": "",
                "vector_db_dir": None,
            }
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["embedding_provider"] == "openai"
    assert cfg["embedding_model"] == "text-embedding-3-large"
    assert cfg["vector_db_dir"] == str(tmp_path / "data" / "biji_vectors")


def test_get_biji_retrieval_config_rejects_invalid_numeric_ranges(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config(
        {
            "biji_retrieval": {
                "chunk_size": 0,
                "chunk_overlap": -5,
                "top_k": 0,
            }
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["chunk_size"] == 700
    assert cfg["chunk_overlap"] == 120
    assert cfg["top_k"] == 10

    storage.save_config(
        {
            "biji_retrieval": {
                "chunk_size": 50,
                "chunk_overlap": 50,
            }
        }
    )

    cfg = storage.get_biji_retrieval_config()

    assert cfg["chunk_size"] == 50
    assert cfg["chunk_overlap"] == 49
