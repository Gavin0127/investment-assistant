"""Tests for core.openai_client.LLMClient."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestLLMClientInit:
    def test_requires_api_key_openai(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENAI_API_KEY", None)
            from core.openai_client import LLMClient
            with pytest.raises(ValueError, match="OPENAI_API_KEY"):
                LLMClient(api_key=None, provider="openai")

    def test_requires_api_key_gemini(self):
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("GEMINI_API_KEY", None)
            from core.openai_client import LLMClient
            with pytest.raises(ValueError, match="GEMINI_API_KEY"):
                LLMClient(api_key=None, provider="gemini")

    def test_accepts_explicit_key_openai(self):
        with patch("core.openai_client.OpenAI"):
            from core.openai_client import LLMClient
            c = LLMClient(api_key="sk-test", provider="openai")
            assert c.api_key == "sk-test"
            assert c.model == "gpt-5.2"
            assert c.provider == "openai"

    def test_accepts_explicit_key_gemini(self):
        with patch("core.openai_client.OpenAI"):
            from core.openai_client import LLMClient
            c = LLMClient(api_key="gem-test", provider="gemini")
            assert c.api_key == "gem-test"
            assert c.model == "gemini-2.5-flash"
            assert c.provider == "gemini"

    def test_default_provider_is_gemini(self):
        with patch("core.openai_client.OpenAI"):
            from core.openai_client import LLMClient
            c = LLMClient(api_key="gem-test")
            assert c.provider == "gemini"
            assert c.model == "gemini-2.5-flash"

    def test_custom_model_override(self):
        with patch("core.openai_client.OpenAI"):
            from core.openai_client import LLMClient
            c = LLMClient(api_key="gem-test", model="gemini-3.1-pro", provider="gemini")
            assert c.model == "gemini-3.1-pro"

    def test_gemini_sets_base_url(self):
        with patch("core.openai_client.OpenAI") as mock_cls:
            from core.openai_client import LLMClient
            LLMClient(api_key="gem-test", provider="gemini")
            call_kwargs = mock_cls.call_args.kwargs
            assert "base_url" in call_kwargs
            assert "generativelanguage.googleapis.com" in call_kwargs["base_url"]

    def test_openai_no_base_url(self):
        with patch("core.openai_client.OpenAI") as mock_cls:
            from core.openai_client import LLMClient
            LLMClient(api_key="sk-test", provider="openai")
            call_kwargs = mock_cls.call_args.kwargs
            assert "base_url" not in call_kwargs

    def test_env_key_fallback_openai(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env"}):
            with patch("core.openai_client.OpenAI"):
                from core.openai_client import LLMClient
                c = LLMClient(provider="openai")
                assert c.api_key == "sk-env"

    def test_env_key_fallback_gemini(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "gem-env"}):
            with patch("core.openai_client.OpenAI"):
                from core.openai_client import LLMClient
                c = LLMClient(provider="gemini")
                assert c.api_key == "gem-env"

    def test_backward_compat_alias(self):
        from core.openai_client import OpenAIClient, LLMClient
        assert OpenAIClient is LLMClient


# ---------------------------------------------------------------------------
# chat / chat_with_system
# ---------------------------------------------------------------------------

class TestChat:
    def test_chat_returns_content(self, mock_openai_client):
        result = mock_openai_client.chat("hello")
        assert result == "mock response"

    def test_chat_with_system(self, mock_openai_client):
        result = mock_openai_client.chat_with_system("sys", "usr")
        assert result == "mock response"

    def test_chat_passes_history(self, mock_openai_client):
        mock_openai_client.chat("q", history=[
            {"role": "user", "content": "prev"},
            {"role": "model", "content": "ans"},
        ])
        call_args = mock_openai_client.client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        roles = [m["role"] for m in messages]
        assert "assistant" in roles  # 'model' mapped to 'assistant'

    def test_gemini_chat_works(self, mock_gemini_client):
        result = mock_gemini_client.chat("hello")
        assert result == "mock response"


# ---------------------------------------------------------------------------
# search (stub)
# ---------------------------------------------------------------------------

class TestSearch:
    def test_search_returns_disabled_message(self, mock_openai_client):
        result = mock_openai_client.search("test query")
        assert "[search disabled]" in result


# ---------------------------------------------------------------------------
# RSS fetch
# ---------------------------------------------------------------------------

class TestRSSFetch:
    def test_fetch_google_news_rss_network_error(self, mock_openai_client):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            items, err = mock_openai_client._fetch_google_news_rss("q", 7)
            assert items == []
            assert "timeout" in err

    def test_fetch_google_news_rss_parses_xml(self, mock_openai_client):
        xml_body = b"""<?xml version="1.0" encoding="UTF-8"?>
        <rss><channel>
          <item>
            <title>Test News Title</title>
            <link>https://example.com/news1</link>
            <pubDate>Mon, 03 Feb 2026 10:00:00 GMT</pubDate>
            <source>TestSource</source>
          </item>
        </channel></rss>"""

        mock_resp = MagicMock()
        mock_resp.read.return_value = xml_body
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_resp):
            items, err = mock_openai_client._fetch_google_news_rss("q", 7)
            assert err is None
            assert len(items) == 1
            assert items[0]["title"] == "Test News Title"
