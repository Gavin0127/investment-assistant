"""tests/test_profit_tracker.py"""
import json
from unittest.mock import patch, MagicMock

import pytest

from core.profit_tracker import ProfitTracker


@pytest.fixture()
def tracker(mock_openai_client, tmp_storage, tmp_path):
    db_path = str(tmp_path / "test_prices.db")
    return ProfitTracker(mock_openai_client, tmp_storage, db_path=db_path)


class TestCreateModelManual:
    def test_save_and_load(self, tracker, tmp_storage):
        """手动创建模型后应能从 storage 读取"""
        config = {
            "stock_name": "中国海油",
            "commodities": [{"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {"base_profit": 1200, "base_commodity_price": 72.5, "sensitivity": 15.2},
        }
        tracker.create_model_manual("cnooc", config)

        loaded = tracker.get_model("cnooc")
        assert loaded is not None
        assert loaded["stock_name"] == "中国海油"
        assert loaded["created_by"] == "manual"

    def test_overwrite_existing(self, tracker):
        """重复创建应覆盖旧模型"""
        config1 = {
            "stock_name": "Test",
            "commodities": [{"name": "X", "symbol": "X=F", "source": "yfinance", "unit": "USD"}],
            "annualization": "Qx4",
            "base_period": "2026Q1",
            "parameters": {"base_profit": 100, "base_commodity_price": 50, "sensitivity": 1},
        }
        config2 = {**config1, "stock_name": "Test Updated"}
        tracker.create_model_manual("test", config1)
        tracker.create_model_manual("test", config2)
        assert tracker.get_model("test")["stock_name"] == "Test Updated"


class TestDeriveModelWithLLM:
    def test_derive_saves_model(self, tracker, tmp_storage, mock_openai_client):
        """LLM 推导应保存模型配置"""
        tmp_storage.save_stock_playbook("cnooc", {
            "stock_name": "中国海油",
            "ticker": "0883.HK",
            "core_thesis": {"summary": "油价敏感型央企"},
            "related_entities": ["Brent原油"],
        })
        llm_response = '''```json
{
    "stock_name": "中国海油",
    "commodities": [{"name": "Brent原油", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
    "annualization": "H1x2",
    "base_period": "2026H1",
    "parameters": {"base_profit": 1200, "base_commodity_price": 72.5, "sensitivity": 15.2}
}
```'''
        mock_openai_client.client.chat.completions.create.return_value.choices[0].message.content = llm_response

        result = tracker.derive_model_with_llm("cnooc")
        assert result is not None
        assert result["created_by"] == "llm_derived"

        saved = tracker.get_model("cnooc")
        assert saved is not None


class TestListModels:
    def test_list_configured_stocks(self, tracker):
        """应列出所有配置了利润模型的股票"""
        config = {
            "stock_name": "Test",
            "commodities": [{"name": "X", "symbol": "X=F", "source": "yfinance", "unit": "USD"}],
            "annualization": "Qx4",
            "base_period": "2026Q1",
            "parameters": {"base_profit": 100, "base_commodity_price": 50, "sensitivity": 1},
        }
        tracker.create_model_manual("stock_a", config)
        tracker.create_model_manual("stock_b", {**config, "stock_name": "Test B"})

        models = tracker.list_models()
        assert len(models) == 2
