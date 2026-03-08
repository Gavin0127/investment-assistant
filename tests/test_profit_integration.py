"""tests/test_profit_integration.py — 集成测试 + E2E 测试"""
import json
import os
import sqlite3
from unittest.mock import patch, MagicMock

import pytest

from core.commodity_price import CommodityPriceService
from core.profit_model import ProfitModel
from core.profit_tracker import ProfitTracker
from core.storage import Storage


# ==================== 集成测试：核心模块协作 ====================

class TestPriceServiceToProfitModel:
    """CommodityPriceService → ProfitModel 集成"""

    def test_cached_prices_feed_into_model(self, tmp_path):
        """从 SQLite 读取的价格数据应能直接传入 ProfitModel 计算"""
        db_path = str(tmp_path / "prices.db")
        svc = CommodityPriceService(db_path=db_path)

        # 写入模拟价格数据
        svc._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-01-02", "open": 70, "high": 72, "low": 69, "close": 71.0, "currency": "USD"},
            {"date": "2026-01-03", "open": 71, "high": 74, "low": 70, "close": 73.0, "currency": "USD"},
            {"date": "2026-01-06", "open": 73, "high": 76, "low": 72, "close": 75.0, "currency": "USD"},
        ])

        # 从 SQLite 读取
        cached = svc.get_cached("BZ=F", "yfinance", "2026-01-01", "2026-01-31")
        assert len(cached) == 3

        # 传入 ProfitModel
        config = {
            "stock_name": "中国海油",
            "commodities": [{"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {"base_profit": 1000, "base_commodity_price": 72.0, "sensitivity": 10.0},
        }
        model = ProfitModel.from_config(config)
        daily = model.calculate({"BZ=F": cached})

        assert len(daily) == 3
        # day1: close=71, delta=-1*10=-10, profit=990, ann=1980
        assert daily[0]["annualized_profit"] == 1980.0
        # day3: close=75, delta=3*10=30, profit=1030, ann=2060
        assert daily[2]["annualized_profit"] == 2060.0

    def test_multi_commodity_integration(self, tmp_path):
        """多原材料场景：多个 symbol 的价格数据协同计算"""
        db_path = str(tmp_path / "prices.db")
        svc = CommodityPriceService(db_path=db_path)

        svc._upsert_prices("HG=F", "yfinance", [
            {"date": "2026-01-02", "close": 95000, "currency": "USD"},
        ])
        svc._upsert_prices("GC=F", "yfinance", [
            {"date": "2026-01-02", "close": 1100, "currency": "USD"},
        ])

        config = {
            "stock_name": "紫金矿业",
            "commodities": [
                {"name": "铜", "symbol": "HG=F", "source": "yfinance", "unit": "USD/lb"},
                {"name": "黄金", "symbol": "GC=F", "source": "yfinance", "unit": "USD/oz"},
            ],
            "annualization": "Qx4",
            "base_period": "2026Q1",
            "parameters": {
                "copper": {"base_profit": 450, "base_price": 95000, "sensitivity": 3.2},
                "gold": {"base_profit": 280, "base_price": 1050, "sensitivity": 0.8},
            },
        }
        model = ProfitModel.from_config(config)
        prices = {
            "HG=F": svc.get_cached("HG=F", "yfinance", "2026-01-01", "2026-01-31"),
            "GC=F": svc.get_cached("GC=F", "yfinance", "2026-01-01", "2026-01-31"),
        }
        daily = model.calculate(prices)
        assert len(daily) == 1
        # copper: 450 + 0*3.2 = 450, gold: 280 + 50*0.8 = 320, total=770, ann=3080
        assert daily[0]["annualized_profit"] == 3080.0


class TestProfitTrackerIntegration:
    """ProfitTracker 端到端集成（手动配置 → 价格写入 → 利润计算）"""

    @pytest.fixture()
    def setup(self, tmp_path, mock_openai_client):
        storage = Storage(base_dir=str(tmp_path / "inv"))
        db_path = str(tmp_path / "prices.db")
        tracker = ProfitTracker(mock_openai_client, storage, db_path=db_path)
        return tracker, storage

    def test_manual_config_then_calculate(self, setup):
        """手动配置模型 → 写入价格 → 获取日度利润"""
        tracker, storage = setup

        # 1. 手动配置
        config = {
            "stock_name": "云铝股份",
            "commodities": [
                {"name": "铝", "symbol": "ALI=F", "source": "yfinance", "unit": "USD/ton"},
            ],
            "annualization": "Qx4",
            "base_period": "2026Q1",
            "parameters": {"base_profit": 40, "base_commodity_price": 23000, "sensitivity": 0.005},
        }
        tracker.create_model_manual("yunnan_aluminium", config)

        # 2. 写入价格数据
        tracker.price_service._upsert_prices("ALI=F", "yfinance", [
            {"date": "2026-01-02", "close": 23000, "currency": "USD"},
            {"date": "2026-01-03", "close": 24000, "currency": "USD"},
            {"date": "2026-01-06", "close": 25000, "currency": "USD"},
        ])

        # 3. 获取日度利润
        daily = tracker.get_daily_profit("yunnan_aluminium", "2026-01-01", "2026-01-31")
        assert len(daily) == 3
        # day1: delta=0, profit=40, ann=160
        assert daily[0]["annualized_profit"] == 160.0
        # day2: delta=1000*0.005=5, profit=45, ann=180
        assert daily[1]["annualized_profit"] == 180.0

        # 4. 获取三场景汇总
        summary = tracker.get_summary("yunnan_aluminium", "2026-01-01", "2026-01-31")
        assert len(summary) == 3
        assert summary[0]["scenario"] == "最新价格"

    def test_list_models_after_create(self, setup):
        """创建多个模型后 list_models 应返回全部"""
        tracker, _ = setup
        base = {
            "commodities": [{"name": "X", "symbol": "X=F", "source": "yfinance", "unit": "USD"}],
            "annualization": "Qx4", "base_period": "2026Q1",
            "parameters": {"base_profit": 100, "base_commodity_price": 50, "sensitivity": 1},
        }
        tracker.create_model_manual("stock_a", {**base, "stock_name": "A"})
        tracker.create_model_manual("stock_b", {**base, "stock_name": "B"})
        tracker.create_model_manual("stock_c", {**base, "stock_name": "C"})

        models = tracker.list_models()
        names = sorted([m["stock_name"] for m in models])
        assert names == ["A", "B", "C"]


# ==================== E2E 测试：Web API ====================

class TestWebAPIProfitTracker:
    """Flask API 端到端测试"""

    @pytest.fixture()
    def app_client(self, tmp_path):
        """创建测试用 Flask app"""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

        import web.app as webapp

        # 用临时目录替换 storage
        webapp.storage = Storage(base_dir=str(tmp_path / "inv"))
        webapp.app.config['TESTING'] = True
        webapp.app.config['SECRET_KEY'] = 'test'

        # mock LLM client 和 profit_tracker
        with patch("core.openai_client.OpenAI"):
            from core.openai_client import LLMClient
            mock_client = LLMClient(api_key="test-key", provider="openai")

        db_path = str(tmp_path / "prices.db")
        webapp.client = mock_client
        webapp.profit_tracker = ProfitTracker(mock_client, webapp.storage, db_path=db_path)

        with webapp.app.test_client() as client:
            yield client, webapp

    def test_list_models_empty(self, app_client):
        """无模型时 /api/profit/models 应返回空列表"""
        client, _ = app_client
        resp = client.get('/api/profit/models')
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_create_model_and_get(self, app_client):
        """POST 创建模型后 GET 应能获取"""
        client, webapp = app_client
        config = {
            "stock_name": "测试股",
            "commodities": [{"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {"base_profit": 500, "base_commodity_price": 70, "sensitivity": 8},
            "mode": "manual",
        }
        resp = client.post('/api/profit/test_stock/model',
                           data=json.dumps(config),
                           content_type='application/json')
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # GET 模型
        resp = client.get('/api/profit/test_stock/model')
        assert resp.status_code == 200
        model = resp.get_json()
        assert model["stock_name"] == "测试股"
        assert model["created_by"] == "manual"

    def test_get_profit_data_with_prices(self, app_client):
        """写入价格后 GET /api/profit/<id> 应返回 daily + summary"""
        client, webapp = app_client

        # 创建模型
        config = {
            "stock_name": "测试股",
            "commodities": [{"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {"base_profit": 500, "base_commodity_price": 70, "sensitivity": 8},
            "mode": "manual",
        }
        client.post('/api/profit/test_stock/model',
                     data=json.dumps(config),
                     content_type='application/json')

        # 写入价格数据
        webapp.profit_tracker.price_service._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-01-02", "close": 70.0, "currency": "USD"},
            {"date": "2026-01-03", "close": 75.0, "currency": "USD"},
            {"date": "2026-01-06", "close": 80.0, "currency": "USD"},
        ])

        resp = client.get('/api/profit/test_stock?start=2026-01-01&end=2026-01-31')
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["daily"]) == 3
        assert len(data["summary"]) == 3
        assert data["model"]["stock_name"] == "测试股"

    def test_list_models_after_create(self, app_client):
        """创建模型后 /api/profit/models 应包含该模型"""
        client, _ = app_client
        config = {
            "stock_name": "测试股",
            "commodities": [{"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {"base_profit": 500, "base_commodity_price": 70, "sensitivity": 8},
            "mode": "manual",
        }
        client.post('/api/profit/test_stock/model',
                     data=json.dumps(config),
                     content_type='application/json')

        resp = client.get('/api/profit/models')
        models = resp.get_json()
        assert len(models) == 1
        assert models[0]["stock_name"] == "测试股"

    def test_refresh_prices_empty(self, app_client):
        """无模型时刷新应返回 refreshed=0"""
        client, _ = app_client
        resp = client.post('/api/profit/refresh')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["refreshed"] == 0

    def test_profit_dashboard_page(self, app_client):
        """Dashboard 页面应可访问"""
        client, _ = app_client
        resp = client.get('/profit-dashboard')
        assert resp.status_code == 200
        assert b'profitDashboard' in resp.data

    def test_get_nonexistent_model(self, app_client):
        """不存在的模型应返回空对象"""
        client, _ = app_client
        resp = client.get('/api/profit/nonexistent/model')
        assert resp.status_code == 200
        assert resp.get_json() == {}

    def test_get_profit_data_no_model(self, app_client):
        """无模型时 GET /api/profit/<id> 应返回空数据"""
        client, _ = app_client
        resp = client.get('/api/profit/nonexistent')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["daily"] == []
        assert data["summary"] == []
        assert data["model"] is None
