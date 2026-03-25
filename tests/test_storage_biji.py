from core.storage import Storage


def test_get_biji_config_reads_local_settings(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "enabled": True,
            "auth_mode": "bearer",
            "api_base": "https://notes-api.biji.com",
            "bearer_token": " secret-token ",
            "browser_profile_dir": "~/custom-biji-browser",
            "page_size": 50,
            "download_images": True,
        }
    })

    cfg = storage.get_biji_config()

    assert cfg["enabled"] is True
    assert cfg["auth_mode"] == "bearer"
    assert cfg["api_base"] == "https://notes-api.biji.com"
    assert cfg["bearer_token"] == " secret-token "
    assert cfg["browser_profile_dir"] == "~/custom-biji-browser"
    assert cfg["page_size"] == 50
    assert cfg["download_images"] is True


def test_get_biji_config_defaults_to_browser_session_and_profile_dir(tmp_path):
    storage = Storage(base_dir=str(tmp_path))

    cfg = storage.get_biji_config()

    assert cfg["auth_mode"] == "browser_session"
    assert cfg["browser_profile_dir"] == str(tmp_path / "data" / "biji_browser")


def test_get_biji_config_falls_back_to_browser_session_for_unknown_mode(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "auth_mode": "mystery-mode",
        }
    })

    cfg = storage.get_biji_config()

    assert cfg["auth_mode"] == "browser_session"


def test_get_biji_token_returns_stripped_value(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "bearer_token": " secret-token ",
        }
    })

    assert storage.get_biji_token() == "secret-token"


def test_get_biji_config_parses_boolean_like_strings_safely(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "enabled": "false",
            "download_images": "0",
        }
    })

    cfg = storage.get_biji_config()

    assert cfg["enabled"] is False
    assert cfg["download_images"] is False


def test_get_biji_config_falls_back_when_page_size_is_none_or_invalid(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "page_size": None,
        }
    })

    assert storage.get_biji_config()["page_size"] == 50

    storage.save_config({
        "biji": {
            "page_size": "oops",
        }
    })

    assert storage.get_biji_config()["page_size"] == 50


def test_get_biji_token_returns_none_when_missing(tmp_path):
    storage = Storage(base_dir=str(tmp_path))

    assert storage.get_biji_token() is None


def test_get_biji_token_returns_none_for_whitespace_only_value(tmp_path):
    storage = Storage(base_dir=str(tmp_path))
    storage.save_config({
        "biji": {
            "bearer_token": "   ",
        }
    })

    assert storage.get_biji_token() is None
