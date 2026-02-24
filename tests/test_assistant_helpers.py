"""Tests for assistant.py helper methods (_extract_json, _deep_merge)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_assistant():
    """Create an InvestmentAssistant with fully mocked dependencies."""
    with patch("assistant.Storage") as MockStorage, \
         patch("assistant.LLMClient") as MockClient, \
         patch("assistant.InterviewManager"), \
         patch("assistant.EnvironmentCollector"), \
         patch("assistant.ResearchEngine"), \
         patch("assistant.Display") as MockDisplay:

        mock_storage = MagicMock()
        mock_storage.get_api_key.return_value = "sk-test"
        mock_storage.get_llm_provider.return_value = "gemini"
        mock_storage.get_llm_model.return_value = None
        MockStorage.return_value = mock_storage

        mock_client = MagicMock()
        mock_client.model = "gpt-5.2"
        MockClient.return_value = mock_client

        MockDisplay.return_value = MagicMock()

        from assistant import InvestmentAssistant
        return InvestmentAssistant()


class TestExtractJson:
    def test_plain_json(self):
        a = _make_assistant()
        data = {"key": "value"}
        result = a._extract_json(json.dumps(data))
        assert result == data

    def test_fenced_code_block(self):
        a = _make_assistant()
        text = "Here:\n```json\n{\"a\": 1}\n```\nDone."
        result = a._extract_json(text)
        assert result == {"a": 1}

    def test_brace_match(self):
        a = _make_assistant()
        text = "blah {\"x\": 2} blah"
        result = a._extract_json(text)
        assert result == {"x": 2}

    def test_empty_returns_none(self):
        a = _make_assistant()
        assert a._extract_json("") is None
        assert a._extract_json(None) is None

    def test_invalid_json_returns_none(self):
        a = _make_assistant()
        assert a._extract_json("not json at all") is None

    def test_oversized_input_returns_none(self):
        a = _make_assistant()
        big = "x" * (a._MAX_JSON_INPUT_SIZE + 1)
        assert a._extract_json(big) is None


class TestDeepMerge:
    def test_shallow_merge(self):
        a = _make_assistant()
        base = {"a": 1, "b": 2}
        patch_data = {"b": 3, "c": 4}
        result = a._deep_merge(base, patch_data)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self):
        a = _make_assistant()
        base = {"x": {"y": 1, "z": 2}}
        patch_data = {"x": {"z": 3, "w": 4}}
        result = a._deep_merge(base, patch_data)
        assert result == {"x": {"y": 1, "z": 3, "w": 4}}

    def test_list_replaces(self):
        a = _make_assistant()
        base = {"items": [1, 2]}
        patch_data = {"items": [3]}
        result = a._deep_merge(base, patch_data)
        assert result == {"items": [3]}

    def test_protected_fields_ignored(self):
        a = _make_assistant()
        base = {"created_at": "2026-01-01", "name": "old"}
        patch_data = {"created_at": "2099-01-01", "name": "new", "updated_at": "2099-01-01"}
        result = a._deep_merge(base, patch_data)
        assert result["created_at"] == "2026-01-01"  # not overwritten
        assert result["name"] == "new"
        assert "updated_at" not in result or result["updated_at"] == "2026-01-01"

    def test_none_base(self):
        a = _make_assistant()
        result = a._deep_merge(None, {"a": 1})
        assert result == {"a": 1}

    def test_none_patch(self):
        a = _make_assistant()
        result = a._deep_merge({"a": 1}, None)
        assert result == {"a": 1}
