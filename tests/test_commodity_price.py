"""tests/test_commodity_price.py"""
import sqlite3
from datetime import date, datetime
from unittest.mock import patch, MagicMock

import pytest

from core.commodity_price import CommodityPriceService


@pytest.fixture()
def price_service(tmp_path):
    db_path = str(tmp_path / "test_prices.db")
    return CommodityPriceService(db_path=db_path)


class TestSQLiteStorage:
    def test_init_creates_table(self, price_service):
        """初始化时应创建 commodity_prices 表"""
        conn = sqlite3.connect(price_service.db_path)
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='commodity_prices'"
        )
        assert cursor.fetchone() is not None
        conn.close()

    def test_upsert_and_get_cached(self, price_service):
        """写入价格数据后应能读取"""
        price_service._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-01-15", "open": 80.0, "high": 82.0, "low": 79.0,
             "close": 81.5, "currency": "USD"},
            {"date": "2026-01-16", "open": 81.5, "high": 83.0, "low": 80.0,
             "close": 82.0, "currency": "USD"},
        ])
        rows = price_service.get_cached("BZ=F", "yfinance", "2026-01-01", "2026-01-31")
        assert len(rows) == 2
        assert rows[0]["close"] == 81.5
        assert rows[1]["date"] == "2026-01-16"

    def test_upsert_overwrites_existing(self, price_service):
        """重复写入同一日期应覆盖"""
        price_service._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-01-15", "open": 80.0, "high": 82.0, "low": 79.0,
             "close": 81.5, "currency": "USD"},
        ])
        price_service._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-01-15", "open": 80.0, "high": 82.0, "low": 79.0,
             "close": 99.0, "currency": "USD"},
        ])
        rows = price_service.get_cached("BZ=F", "yfinance", "2026-01-01", "2026-01-31")
        assert len(rows) == 1
        assert rows[0]["close"] == 99.0

    def test_get_cached_empty(self, price_service):
        """无数据时返回空列表"""
        rows = price_service.get_cached("NONE", "yfinance", "2026-01-01", "2026-12-31")
        assert rows == []


class TestFetchDaily:
    @patch("core.commodity_price.yf")
    def test_fetch_daily_yfinance(self, mock_yf, price_service):
        """yfinance 数据源应正确拉取并存储"""
        import pandas as pd
        mock_df = pd.DataFrame({
            "Open": [80.0, 81.0],
            "High": [82.0, 83.0],
            "Low": [79.0, 80.0],
            "Close": [81.5, 82.0],
        }, index=pd.to_datetime(["2026-01-15", "2026-01-16"]))
        mock_yf.download.return_value = mock_df

        rows = price_service.fetch_daily("BZ=F", "yfinance", "2026-01-15", "2026-01-16")
        assert len(rows) == 2
        mock_yf.download.assert_called_once()

    @patch("core.commodity_price.ak")
    def test_fetch_daily_akshare(self, mock_ak, price_service):
        """akshare 数据源应正确拉取碳酸锂数据"""
        import pandas as pd
        mock_df = pd.DataFrame({
            "date": ["2026-01-15", "2026-01-16"],
            "open": [140000, 141000],
            "high": [142000, 143000],
            "low": [139000, 140000],
            "close": [141000, 142000],
        })
        mock_ak.futures_main_sina.return_value = mock_df

        rows = price_service.fetch_daily("LC0", "akshare", "2026-01-15", "2026-01-16")
        assert len(rows) == 2
        assert rows[0]["currency"] == "CNY"


class TestFetchLatest:
    def test_fetch_latest_from_cache(self, price_service):
        """有缓存时应返回最新缓存价格"""
        price_service._upsert_prices("BZ=F", "yfinance", [
            {"date": "2026-03-07", "open": 84.0, "high": 86.0, "low": 83.0,
             "close": 85.41, "currency": "USD"},
        ])
        latest = price_service.fetch_latest("BZ=F", "yfinance")
        assert latest == 85.41
