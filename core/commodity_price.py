"""大宗商品价格采集与存储（SQLite）"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime
from typing import List, Dict, Optional

import yfinance as yf

try:
    import akshare as ak
except ImportError:
    ak = None  # type: ignore

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.expanduser("~/.investment-assistant/data/commodity_prices.db")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS commodity_prices (
    symbol     TEXT NOT NULL,
    source     TEXT NOT NULL,
    date       TEXT NOT NULL,
    open       REAL,
    high       REAL,
    low        REAL,
    close      REAL NOT NULL,
    currency   TEXT DEFAULT 'USD',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, source, date)
);
"""

_UPSERT_SQL = """
INSERT INTO commodity_prices (symbol, source, date, open, high, low, close, currency, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(symbol, source, date) DO UPDATE SET
    open=excluded.open, high=excluded.high, low=excluded.low,
    close=excluded.close, currency=excluded.currency, updated_at=excluded.updated_at;
"""


class CommodityPriceService:
    """大宗商品价格采集 + SQLite 缓存"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or _DEFAULT_DB_PATH
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(_CREATE_TABLE_SQL)

    def _upsert_prices(self, symbol: str, source: str, rows: List[Dict]):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(_UPSERT_SQL, [
                (symbol, source, r["date"], r.get("open"), r.get("high"),
                 r.get("low"), r["close"], r.get("currency", "USD"), now)
                for r in rows
            ])

    def get_cached(self, symbol: str, source: str, start: str, end: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM commodity_prices WHERE symbol=? AND source=? AND date>=? AND date<=? ORDER BY date",
                (symbol, source, start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    def fetch_daily(self, symbol: str, source: str, start: str, end: str) -> List[Dict]:
        if source == "akshare":
            rows = self._fetch_akshare(symbol, start, end)
        else:
            rows = self._fetch_yfinance(symbol, start, end)
        if rows:
            self._upsert_prices(symbol, source, rows)
        return rows

    def _fetch_yfinance(self, symbol: str, start: str, end: str) -> List[Dict]:
        df = yf.download(symbol, start=start, end=end, progress=False)
        if df.empty:
            return []
        if hasattr(df.columns, "levels") and len(df.columns.levels) > 1:
            df.columns = df.columns.get_level_values(0)
        rows = []
        for idx, row in df.iterrows():
            rows.append({
                "date": idx.strftime("%Y-%m-%d"),
                "open": float(row.get("Open", 0)),
                "high": float(row.get("High", 0)),
                "low": float(row.get("Low", 0)),
                "close": float(row.get("Close", 0)),
                "currency": "USD",
            })
        return rows

    def _fetch_akshare(self, symbol: str, start: str, end: str) -> List[Dict]:
        if ak is None:
            logger.warning("akshare 未安装，跳过 %s", symbol)
            return []
        df = ak.futures_main_sina(symbol=symbol)
        if df is None or df.empty:
            return []
        col_map = {}
        for c in df.columns:
            cl = c.lower()
            if cl in ("date", "日期"):
                col_map[c] = "date"
            elif cl in ("open", "开盘价"):
                col_map[c] = "open"
            elif cl in ("high", "最高价"):
                col_map[c] = "high"
            elif cl in ("low", "最低价"):
                col_map[c] = "low"
            elif cl in ("close", "收盘价"):
                col_map[c] = "close"
        df = df.rename(columns=col_map)
        df["date"] = df["date"].astype(str).str[:10]
        df = df[(df["date"] >= start) & (df["date"] <= end)]
        rows = []
        for _, row in df.iterrows():
            rows.append({
                "date": row["date"],
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row["close"]),
                "currency": "CNY",
            })
        return rows

    def fetch_latest(self, symbol: str, source: str) -> Optional[float]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT close FROM commodity_prices WHERE symbol=? AND source=? ORDER BY date DESC LIMIT 1",
                (symbol, source),
            ).fetchone()
        return row[0] if row else None

    def refresh_all(self, symbols: List[Dict], start: str, end: str):
        for item in symbols:
            try:
                self.fetch_daily(item["symbol"], item["source"], start, end)
                logger.info("刷新成功: %s (%s)", item["symbol"], item["source"])
            except Exception as e:
                logger.error("刷新失败: %s (%s): %s", item["symbol"], item["source"], e)
