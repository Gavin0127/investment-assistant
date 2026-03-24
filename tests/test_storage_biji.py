from core.storage import Storage


def test_get_biji_config_reads_local_settings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "enabled": True,
            "api_base": "https://notes-api.biji.com",
            "bearer_token": " secret-token ",
            "page_size": 50,
            "download_images": True,
        }
    })

    cfg = storage.get_biji_config()

    assert cfg["enabled"] is True
    assert cfg["api_base"] == "https://notes-api.biji.com"
    assert cfg["bearer_token"] == " secret-token "
    assert cfg["page_size"] == 50
    assert cfg["download_images"] is True


def test_get_biji_token_returns_stripped_value(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "bearer_token": " secret-token ",
        }
    })

    assert storage.get_biji_token() == "secret-token"


def test_get_biji_token_returns_none_when_missing(tmp_path):
    storage = Storage(base_dir=str(tmp_path))

    assert storage.get_biji_token() is None
