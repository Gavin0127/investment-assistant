# 利润跟踪模块实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 实现原材料价格 → 企业利润敏感性跟踪模块，支持手动配置和 LLM 推导利润模型，每日自动拉取大宗商品价格，Web 端展示日度年化利润曲线。

**Architecture:** 新增 `core/profit_tracker.py` 作为核心模块（依赖注入模式），SQLite 存储价格数据，`profit_model.json` 存储每只股票的利润模型配置。Web 端新增 dashboard 页面和详情页嵌入，ECharts 渲染双线折线图。

**Tech Stack:** Python 3.10+, yfinance, akshare, SQLite3 (标准库), Flask, ECharts (CDN), Alpine.js, Tailwind CSS

**设计文档:** `docs/plans/2026-03-08-profit-tracker-design.md`

---

### Task 1: 添加依赖

**Files:**
- Modify: `pyproject.toml:8-15`

**Step 1: 添加 yfinance 和 akshare 依赖**

在 `pyproject.toml` 的 `dependencies` 列表中添加：

```toml
dependencies = [
    "openai>=1.40.0",
    "rich>=13.0.0",
    "prompt-toolkit>=3.0.0",
    "flask>=3.0.0",
    "tavily-python>=0.5.0",
    "requests>=2.31.0",
    "yfinance>=0.2.0",
    "akshare>=1.10.0",
]
```

**Step 2: 安装依赖**

Run: `uv sync`
Expected: 成功安装 yfinance 和 akshare

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore: add yfinance and akshare dependencies"
```

---

### Task 2: CommodityPriceService — SQLite 存储 + 价格采集

**Files:**
- Create: `core/commodity_price.py`
- Test: `tests/test_commodity_price.py`

**Step 1: 写 CommodityPriceService 的测试**

```python
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
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_commodity_price.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.commodity_price'`

**Step 3: 实现 CommodityPriceService**

创建 `core/commodity_price.py`：

```python
"""大宗商品价格采集与存储（SQLite）"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, date
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

    # ---------- 写入 ----------

    def _upsert_prices(self, symbol: str, source: str, rows: List[Dict]):
        now = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(_UPSERT_SQL, [
                (symbol, source, r["date"], r.get("open"), r.get("high"),
                 r.get("low"), r["close"], r.get("currency", "USD"), now)
                for r in rows
            ])

    # ---------- 读取 ----------

    def get_cached(self, symbol: str, source: str, start: str, end: str) -> List[Dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM commodity_prices WHERE symbol=? AND source=? AND date>=? AND date<=? ORDER BY date",
                (symbol, source, start, end),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---------- 拉取 ----------

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
        # yfinance 可能返回 MultiIndex columns，取第一层
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
        # 统一列名
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
```

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_commodity_price.py -v`
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add core/commodity_price.py tests/test_commodity_price.py
git commit -m "feat(core): add CommodityPriceService with SQLite storage"
```

---

### Task 3: ProfitModel — 利润计算引擎

**Files:**
- Create: `core/profit_model.py`
- Test: `tests/test_profit_model.py`

**Step 1: 写 ProfitModel 的测试**

```python
"""tests/test_profit_model.py"""
from core.profit_model import ProfitModel


class TestSingleCommodity:
    """单原材料利润模型（如中国海油）"""

    def setup_method(self):
        self.config = {
            "stock_name": "中国海油",
            "commodities": [
                {"name": "Brent原油", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}
            ],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {
                "base_profit": 1200,
                "base_commodity_price": 72.5,
                "sensitivity": 15.2
            },
        }
        self.model = ProfitModel.from_config(self.config)

    def test_calculate_at_base_price(self):
        """基准价格时利润应等于基准利润 × 年化乘数"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 72.5},
        ]}
        result = self.model.calculate(prices)
        # H1x2: 1200 * 2 = 2400
        assert result[0]["annualized_profit"] == 2400.0

    def test_calculate_price_increase(self):
        """价格上涨时利润应增加"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 82.5},  # +10
        ]}
        result = self.model.calculate(prices)
        # base=1200, delta=10*15.2=152, period_profit=1352, annualized=1352*2=2704
        assert result[0]["annualized_profit"] == 2704.0

    def test_calculate_ytd_avg(self):
        """年内均价年化利润应基于累计均价"""
        prices = {"BZ=F": [
            {"date": "2026-01-15", "close": 70.0},
            {"date": "2026-01-16", "close": 75.0},
        ]}
        result = self.model.calculate(prices)
        # day1: avg=70, delta=-2.5*15.2=-38, profit=1162, ann=2324
        assert result[0]["ytd_avg_annualized_profit"] == 2324.0
        # day2: avg=72.5, delta=0, profit=1200, ann=2400
        assert result[1]["ytd_avg_annualized_profit"] == 2400.0


class TestMultiCommodity:
    """多原材料利润模型（如紫金矿业）"""

    def setup_method(self):
        self.config = {
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
        self.model = ProfitModel.from_config(self.config)

    def test_multi_commodity_at_base(self):
        """多原材料基准价格时利润 = 各子项之和 × 年化乘数"""
        prices = {
            "HG=F": [{"date": "2026-01-15", "close": 95000}],
            "GC=F": [{"date": "2026-01-15", "close": 1050}],
        }
        result = self.model.calculate(prices)
        # (450+280)*4 = 2920
        assert result[0]["annualized_profit"] == 2920.0


class TestScenarios:
    """三场景汇总"""

    def setup_method(self):
        self.config = {
            "stock_name": "测试",
            "commodities": [
                {"name": "Brent", "symbol": "BZ=F", "source": "yfinance", "unit": "USD/bbl"}
            ],
            "annualization": "H1x2",
            "base_period": "2026H1",
            "parameters": {
                "base_profit": 1000,
                "base_commodity_price": 80.0,
                "sensitivity": 10.0
            },
        }
        self.model = ProfitModel.from_config(self.config)

    def test_scenarios_returns_three(self):
        """scenarios 应返回三个场景"""
        prices = {"BZ=F": [
            {"date": "2026-02-01", "close": 80.0},
            {"date": "2026-02-02", "close": 85.0},
            {"date": "2026-03-06", "close": 90.0},
        ]}
        result = self.model.scenarios(prices)
        assert len(result) == 3
        assert result[0]["scenario"] == "最新价格"
        assert result[1]["scenario"] == "最近1个月均价"
        assert result[2]["scenario"] == "年初至今均价年化"
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_profit_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.profit_model'`

**Step 3: 实现 ProfitModel**

创建 `core/profit_model.py`：

```python
"""利润计算引擎"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional


# 年化乘数
_ANNUALIZATION_MULTIPLIER = {"H1x2": 2, "Qx4": 4, "annual": 1}


class ProfitModel:
    """基于原材料价格的利润敏感性模型"""

    def __init__(self, config: Dict):
        self.config = config
        self.commodities = config["commodities"]
        self.multiplier = _ANNUALIZATION_MULTIPLIER.get(config.get("annualization", "Qx4"), 4)
        self.params = config["parameters"]
        self._is_multi = isinstance(next(iter(self.params.values())), dict) if self.params else False

    @classmethod
    def from_config(cls, config: Dict) -> "ProfitModel":
        return cls(config)

    def _period_profit_single(self, commodity_price: float) -> float:
        """单原材料：计算基准期利润"""
        base_profit = self.params["base_profit"]
        base_price = self.params["base_commodity_price"]
        sensitivity = self.params["sensitivity"]
        return base_profit + (commodity_price - base_price) * sensitivity

    def _period_profit_multi(self, prices_by_name: Dict[str, float]) -> float:
        """多原材料：各子项利润之和"""
        total = 0.0
        for commodity in self.commodities:
            name_key = self._commodity_param_key(commodity)
            sub = self.params.get(name_key, {})
            if not sub:
                continue
            price = prices_by_name.get(commodity["symbol"], sub.get("base_price", 0))
            base_profit = sub["base_profit"]
            base_price = sub["base_price"]
            sensitivity = sub["sensitivity"]
            total += base_profit + (price - base_price) * sensitivity
        return total

    def _commodity_param_key(self, commodity: Dict) -> str:
        """从 commodities 条目推导 parameters 中的 key"""
        name = commodity["name"].lower()
        for key in self.params:
            if key.lower() in name or name in key.lower():
                return key
        return commodity["symbol"].lower().replace("=", "").replace(".", "")

    def calculate(self, prices: Dict[str, List[Dict]]) -> List[Dict]:
        """计算日度利润序列

        Args:
            prices: {symbol: [{date, close, ...}, ...]} 按日期升序

        Returns:
            [{date, annualized_profit, ytd_avg_annualized_profit, commodity_prices}, ...]
        """
        if self._is_multi:
            return self._calculate_multi(prices)
        return self._calculate_single(prices)

    def _calculate_single(self, prices: Dict[str, List[Dict]]) -> List[Dict]:
        symbol = self.commodities[0]["symbol"]
        rows = prices.get(symbol, [])
        result = []
        running_sum = 0.0
        for i, row in enumerate(rows):
            price = row["close"]
            running_sum += price
            avg_price = running_sum / (i + 1)

            period_profit = self._period_profit_single(price)
            avg_period_profit = self._period_profit_single(avg_price)

            result.append({
                "date": row["date"],
                "annualized_profit": round(period_profit * self.multiplier, 2),
                "ytd_avg_annualized_profit": round(avg_period_profit * self.multiplier, 2),
                "commodity_prices": {symbol: price},
            })
        return result

    def _calculate_multi(self, prices: Dict[str, List[Dict]]) -> List[Dict]:
        # 以第一个 commodity 的日期序列为基准
        primary = self.commodities[0]["symbol"]
        primary_rows = prices.get(primary, [])
        # 构建各 symbol 的日期索引
        price_index: Dict[str, Dict[str, float]] = {}
        running_sums: Dict[str, float] = {}
        running_counts: Dict[str, int] = {}
        for c in self.commodities:
            sym = c["symbol"]
            price_index[sym] = {r["date"]: r["close"] for r in prices.get(sym, [])}
            running_sums[sym] = 0.0
            running_counts[sym] = 0

        result = []
        for row in primary_rows:
            d = row["date"]
            day_prices = {}
            avg_prices = {}
            for c in self.commodities:
                sym = c["symbol"]
                p = price_index[sym].get(d)
                if p is None:
                    # 用最近已知价格
                    sub = self.params.get(self._commodity_param_key(c), {})
                    p = sub.get("base_price", 0)
                day_prices[sym] = p
                running_sums[sym] += p
                running_counts[sym] += 1
                avg_prices[sym] = running_sums[sym] / running_counts[sym]

            period_profit = self._period_profit_multi(day_prices)
            avg_period_profit = self._period_profit_multi(avg_prices)

            result.append({
                "date": d,
                "annualized_profit": round(period_profit * self.multiplier, 2),
                "ytd_avg_annualized_profit": round(avg_period_profit * self.multiplier, 2),
                "commodity_prices": day_prices,
            })
        return result

    def scenarios(self, prices: Dict[str, List[Dict]]) -> List[Dict]:
        """三场景汇总：最新价格 / 近1月均价 / 年初至今均价年化"""
        daily = self.calculate(prices)
        if not daily:
            return []

        latest = daily[-1]

        # 近1月：取最近30天
        last_30 = daily[-30:] if len(daily) >= 30 else daily
        avg_profit_1m = sum(d["annualized_profit"] for d in last_30) / len(last_30)

        # 年初至今
        ytd_profit = daily[-1]["ytd_avg_annualized_profit"]

        # 收集原材料价格
        def avg_commodity_prices(rows):
            if not rows:
                return {}
            result = {}
            for sym in rows[0].get("commodity_prices", {}):
                vals = [r["commodity_prices"].get(sym, 0) for r in rows]
                result[sym] = round(sum(vals) / len(vals), 2)
            return result

        return [
            {
                "scenario": "最新价格",
                "annualized_profit": latest["annualized_profit"],
                "commodity_prices": latest["commodity_prices"],
                "date_range": f"{latest['date']} 至 {latest['date']}",
            },
            {
                "scenario": "最近1个月均价",
                "annualized_profit": round(avg_profit_1m, 2),
                "commodity_prices": avg_commodity_prices(last_30),
                "date_range": f"{last_30[0]['date']} 至 {last_30[-1]['date']}",
            },
            {
                "scenario": "年初至今均价年化",
                "annualized_profit": ytd_profit,
                "commodity_prices": avg_commodity_prices(daily),
                "date_range": f"{daily[0]['date']} 至 {daily[-1]['date']}",
            },
        ]
```

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_profit_model.py -v`
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add core/profit_model.py tests/test_profit_model.py
git commit -m "feat(core): add ProfitModel calculation engine"
```

---

### Task 4: ProfitTracker — 主入口模块（手动配置 + LLM 推导）

**Files:**
- Create: `core/profit_tracker.py`
- Test: `tests/test_profit_tracker.py`

**Step 1: 写 ProfitTracker 的测试**

```python
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
        # 先创建 playbook
        tmp_storage.save_stock_playbook("cnooc", {
            "stock_name": "中国海油",
            "ticker": "0883.HK",
            "core_thesis": {"summary": "油价敏感型央企"},
            "related_entities": ["Brent原油"],
        })
        # mock LLM 返回 JSON
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
```

**Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_profit_tracker.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.profit_tracker'`

**Step 3: 实现 ProfitTracker**

创建 `core/profit_tracker.py`：

```python
"""利润跟踪主入口模块"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from core.commodity_price import CommodityPriceService
from core.profit_model import ProfitModel

logger = logging.getLogger(__name__)

_DEFAULT_DB = os.path.expanduser("~/.investment-assistant/data/commodity_prices.db")


class ProfitTracker:
    """利润跟踪主入口（依赖注入）"""

    def __init__(self, client, storage, db_path: Optional[str] = None):
        self.client = client
        self.storage = storage
        self.price_service = CommodityPriceService(db_path=db_path or _DEFAULT_DB)

    # ---------- 模型管理 ----------

    def _model_path(self, stock_id: str) -> str:
        stock_dir = self.storage._get_stock_dir(stock_id)
        return str(stock_dir / "profit_model.json")

    def get_model(self, stock_id: str) -> Optional[Dict]:
        path = self._model_path(stock_id)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def _save_model(self, stock_id: str, config: Dict):
        path = self._model_path(stock_id)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

    def create_model_manual(self, stock_id: str, config: Dict):
        config["created_by"] = "manual"
        config["created_at"] = datetime.now().isoformat()
        self._save_model(stock_id, config)

    def derive_model_with_llm(self, stock_id: str) -> Optional[Dict]:
        playbook = self.storage.get_stock_playbook(stock_id)
        if not playbook:
            logger.warning("无 playbook，无法推导: %s", stock_id)
            return None

        history = self.storage.get_recent_research(stock_id, limit=2)

        prompt = f"""你是一位量化分析师。根据以下投资逻辑，推导该股票的利润敏感性模型。

## 股票信息
{json.dumps(playbook, ensure_ascii=False, indent=2)}

## 最近研究记录
{json.dumps(history[:2], ensure_ascii=False, indent=2) if history else "（无）"}

## 要求
分析该公司利润与哪些大宗商品价格相关，输出 JSON 配置。

可用数据源和 symbol：
- yfinance: BZ=F(Brent原油), CL=F(WTI), HG=F(铜), ALI=F(铝), GC=F(黄金), SI=F(白银)
- akshare: LC0(碳酸锂)

年化方式：H1x2(半年报×2), Qx4(单季×4), annual(年报)

单原材料格式：
```json
{{
    "stock_name": "...",
    "commodities": [{{"name": "...", "symbol": "...", "source": "yfinance|akshare", "unit": "..."}}],
    "annualization": "H1x2|Qx4|annual",
    "base_period": "2026H1|2026Q1|...",
    "parameters": {{
        "base_profit": 基准期利润(亿元),
        "base_commodity_price": 基准期原材料均价,
        "sensitivity": 每单位价格变动对应的利润变动(亿元)
    }}
}}
```

多原材料格式（parameters 按原材料名分 key）：
```json
{{
    "parameters": {{
        "copper": {{"base_profit": ..., "base_price": ..., "sensitivity": ..., "gross_margin": ...}},
        "gold": {{"base_profit": ..., "base_price": ..., "sensitivity": ...}}
    }}
}}
```

只输出 JSON，不要解释。"""

        response = self.client.chat(prompt)

        # 4 层 fallback 提取 JSON
        config = self._extract_json(response)
        if not config:
            return None

        config["created_by"] = "llm_derived"
        config["created_at"] = datetime.now().isoformat()
        self._save_model(stock_id, config)
        return config

    def _extract_json(self, text: str) -> Optional[Dict]:
        # 1) 末尾代码块
        blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
        if blocks:
            try:
                return json.loads(blocks[-1])
            except json.JSONDecodeError:
                pass
        # 2) 花括号匹配
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        # 3) 直接解析
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 4) 清理尾逗号重试
        if m:
            cleaned = re.sub(r",\s*([}\]])", r"\1", m.group(0))
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
        return None

    def list_models(self) -> List[Dict]:
        result = []
        stocks_dir = self.storage.base_dir / "stocks"
        if stocks_dir.exists():
            for stock_dir in stocks_dir.iterdir():
                if stock_dir.is_dir():
                    model_path = stock_dir / "profit_model.json"
                    if model_path.exists():
                        with open(model_path, "r", encoding="utf-8") as f:
                            model = json.load(f)
                        model["stock_id"] = stock_dir.name
                        result.append(model)
        return result

    # ---------- 利润计算 ----------

    def get_daily_profit(self, stock_id: str, start: str, end: str) -> List[Dict]:
        config = self.get_model(stock_id)
        if not config:
            return []
        model = ProfitModel.from_config(config)

        prices = {}
        for c in config["commodities"]:
            cached = self.price_service.get_cached(c["symbol"], c["source"], start, end)
            if not cached:
                cached_rows = self.price_service.fetch_daily(c["symbol"], c["source"], start, end)
                prices[c["symbol"]] = cached_rows
            else:
                prices[c["symbol"]] = cached
        return model.calculate(prices)

    def get_summary(self, stock_id: str, start: str, end: str) -> List[Dict]:
        config = self.get_model(stock_id)
        if not config:
            return []
        model = ProfitModel.from_config(config)

        prices = {}
        for c in config["commodities"]:
            cached = self.price_service.get_cached(c["symbol"], c["source"], start, end)
            if not cached:
                cached = self.price_service.fetch_daily(c["symbol"], c["source"], start, end)
            prices[c["symbol"]] = cached
        return model.scenarios(prices)
```

**Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_profit_tracker.py -v`
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add core/profit_tracker.py tests/test_profit_tracker.py
git commit -m "feat(core): add ProfitTracker with manual config and LLM derivation"
```

---

### Task 5: Web 后端 — API 路由

**Files:**
- Modify: `web/app.py`

**Step 1: 在 `web/app.py` 中导入 ProfitTracker 并初始化**

在 `web/app.py:16` 的 import 区域添加：

```python
from core.profit_tracker import ProfitTracker
```

在 `web/app.py:78` 的全局变量区域添加：

```python
profit_tracker = None
```

在 `get_client()` 函数（`web/app.py:80`）中，`preference_learner = ...` 之后添加：

```python
            profit_tracker = ProfitTracker(client, storage)
```

**Step 2: 添加利润跟踪 API 路由**

在 `web/app.py` 的批量扫描 API 区域之前，添加以下路由：

```python
# ==================== 利润跟踪 API ====================

@app.route('/profit-dashboard')
@requires_auth
def profit_dashboard():
    """利润跟踪总览"""
    return render_template('profit_dashboard.html')


@app.route('/api/profit/models', methods=['GET'])
def api_list_profit_models():
    """列出所有已配置利润模型的股票"""
    get_client()
    if not profit_tracker:
        return jsonify({'error': 'API Key 未配置'}), 400
    models = profit_tracker.list_models()
    return jsonify(models)


@app.route('/api/profit/<stock_id>', methods=['GET'])
def api_get_profit_data(stock_id):
    """获取单只股票的日度利润数据"""
    get_client()
    if not profit_tracker:
        return jsonify({'error': 'API Key 未配置'}), 400

    start = request.args.get('start', f'{datetime.now().year}-01-01')
    end = request.args.get('end', datetime.now().strftime('%Y-%m-%d'))

    daily = profit_tracker.get_daily_profit(stock_id, start, end)
    summary = profit_tracker.get_summary(stock_id, start, end)
    model = profit_tracker.get_model(stock_id)

    return jsonify({
        'daily': daily,
        'summary': summary,
        'model': model,
    })


@app.route('/api/profit/<stock_id>/model', methods=['GET'])
def api_get_profit_model(stock_id):
    """获取利润模型配置"""
    get_client()
    if not profit_tracker:
        return jsonify({'error': 'API Key 未配置'}), 400
    model = profit_tracker.get_model(stock_id)
    return jsonify(model or {})


@app.route('/api/profit/<stock_id>/model', methods=['POST'])
def api_save_profit_model(stock_id):
    """创建/更新利润模型"""
    get_client()
    if not profit_tracker:
        return jsonify({'error': 'API Key 未配置'}), 400

    data = request.json
    mode = data.pop('mode', 'manual')

    if mode == 'llm':
        result = profit_tracker.derive_model_with_llm(stock_id)
        if not result:
            return jsonify({'error': 'LLM 推导失败'}), 500
        return jsonify({'success': True, 'model': result})
    else:
        profit_tracker.create_model_manual(stock_id, data)
        return jsonify({'success': True})


@app.route('/api/profit/refresh', methods=['POST'])
def api_refresh_prices():
    """手动触发价格数据刷新"""
    get_client()
    if not profit_tracker:
        return jsonify({'error': 'API Key 未配置'}), 400

    models = profit_tracker.list_models()
    symbols = []
    seen = set()
    for m in models:
        for c in m.get('commodities', []):
            key = (c['symbol'], c['source'])
            if key not in seen:
                symbols.append({'symbol': c['symbol'], 'source': c['source']})
                seen.add(key)

    start = f'{datetime.now().year}-01-01'
    end = datetime.now().strftime('%Y-%m-%d')
    profit_tracker.price_service.refresh_all(symbols, start, end)
    return jsonify({'success': True, 'refreshed': len(symbols)})
```

**Step 3: 在导航栏添加利润跟踪入口**

在 `web/templates/base.html` 的导航链接中，在"批量扫描"之后添加：

```html
<a href="/profit-dashboard"
   class="{% if request.path == '/profit-dashboard' %}text-blue-600 bg-blue-50{% else %}text-gray-600 hover:text-gray-900 hover:bg-gray-50{% endif %} px-3 py-2 rounded-lg text-sm font-medium transition-colors">
    利润跟踪
</a>
```

**Step 4: 运行现有测试确认无回归**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 5: Commit**

```bash
git add web/app.py web/templates/base.html
git commit -m "feat(web): add profit tracker API routes and navigation"
```

---

### Task 6: Web 前端 — Dashboard 页面

**Files:**
- Create: `web/templates/profit_dashboard.html`

**Step 1: 创建 Dashboard 模板**

创建 `web/templates/profit_dashboard.html`，继承 `base.html`。使用 Alpine.js 管理状态，ECharts 渲染图表。

关键结构：

```html
{% extends "base.html" %}
{% block title %}利润跟踪{% endblock %}
{% block content %}
<div x-data="profitDashboard()" x-init="init()">
  <!-- 顶部操作栏 -->
  <div class="flex justify-between items-center mb-6">
    <h1 class="text-2xl font-bold text-gray-900">利润跟踪</h1>
    <button @click="refreshAll()" :disabled="refreshing"
            class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm">
      <span x-show="!refreshing">刷新价格数据</span>
      <span x-show="refreshing">刷新中...</span>
    </button>
  </div>

  <!-- 空状态 -->
  <div x-show="models.length === 0 && !loading" class="bg-white rounded-xl shadow-sm border border-gray-100 p-12 text-center">
    <p class="text-gray-500">暂无利润模型配置</p>
    <p class="text-sm text-gray-400 mt-2">在股票详情页中配置利润模型后，数据将在此展示</p>
  </div>

  <!-- 股票卡片网格 -->
  <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
    <template x-for="model in models" :key="model.stock_id">
      <div class="bg-white rounded-xl shadow-sm border border-gray-100">
        <!-- 卡片头部：股票名 + 年化方式 -->
        <div class="p-4 border-b border-gray-100">
          <div class="flex justify-between items-center">
            <h2 class="text-lg font-bold" x-text="model.stock_name"></h2>
            <span class="text-xs text-gray-500" x-text="model.annualization + ' · ' + model.base_period"></span>
          </div>
          <p class="text-xs text-gray-400 mt-1" x-text="model.commodities.map(c => c.name).join(' + ')"></p>
        </div>

        <!-- ECharts 图表容器 -->
        <div class="p-4">
          <div :id="'chart-' + model.stock_id" style="height: 280px;"></div>
        </div>

        <!-- 三场景汇总表 -->
        <div class="px-4 pb-4">
          <table class="w-full text-sm">
            <thead>
              <tr class="text-gray-500 text-xs">
                <th class="text-left py-1">场景</th>
                <th class="text-right py-1">利润</th>
                <th class="text-right py-1">价格窗口</th>
                <th class="text-right py-1">原材料价格</th>
              </tr>
            </thead>
            <tbody>
              <template x-for="s in profitData[model.stock_id]?.summary || []" :key="s.scenario">
                <tr class="border-t border-gray-50">
                  <td class="py-2 text-gray-700" x-text="s.scenario"></td>
                  <td class="py-2 text-right font-medium" x-text="(s.annualized_profit).toFixed(2) + ' 亿元'"></td>
                  <td class="py-2 text-right text-gray-500 text-xs" x-text="s.date_range"></td>
                  <td class="py-2 text-right text-gray-500 text-xs">
                    <template x-for="(price, sym) in s.commodity_prices" :key="sym">
                      <span class="block" x-text="sym + ': ' + price.toFixed(2)"></span>
                    </template>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</div>

<!-- ECharts CDN -->
<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>

<script>
function profitDashboard() {
  return {
    models: [],
    profitData: {},
    loading: true,
    refreshing: false,

    async init() {
      const resp = await fetch('/api/profit/models');
      this.models = await resp.json();
      this.loading = false;
      for (const model of this.models) {
        await this.loadProfitData(model.stock_id);
      }
    },

    async loadProfitData(stockId) {
      const resp = await fetch(`/api/profit/${stockId}`);
      const data = await resp.json();
      this.profitData[stockId] = data;
      this.$nextTick(() => this.renderChart(stockId, data));
    },

    renderChart(stockId, data) {
      const el = document.getElementById('chart-' + stockId);
      if (!el || !data.daily || data.daily.length === 0) return;
      const chart = echarts.init(el);
      const dates = data.daily.map(d => d.date.slice(5)); // MM-DD
      chart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['按当日价格年化利润', '年内均价年化利润'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 60, right: 20, top: 35, bottom: 25 },
        xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
        yAxis: { type: 'value', axisLabel: { fontSize: 10 } },
        series: [
          {
            name: '按当日价格年化利润',
            type: 'line', smooth: true, symbol: 'none',
            lineStyle: { color: '#C07A3E', width: 2 },
            itemStyle: { color: '#C07A3E' },
            data: data.daily.map(d => d.annualized_profit),
          },
          {
            name: '年内均价年化利润',
            type: 'line', smooth: true, symbol: 'none',
            lineStyle: { color: '#3B7A8A', width: 2 },
            itemStyle: { color: '#3B7A8A' },
            data: data.daily.map(d => d.ytd_avg_annualized_profit),
          },
        ],
      });
      window.addEventListener('resize', () => chart.resize());
    },

    async refreshAll() {
      this.refreshing = true;
      await fetch('/api/profit/refresh', { method: 'POST' });
      for (const model of this.models) {
        await this.loadProfitData(model.stock_id);
      }
      this.refreshing = false;
    },
  };
}
</script>
{% endblock %}
```

**Step 2: 验证页面可访问**

手动启动 `uv run python web/app.py`，访问 `http://localhost:5000/profit-dashboard`，确认页面渲染正常（空状态提示）。

**Step 3: Commit**

```bash
git add web/templates/profit_dashboard.html
git commit -m "feat(web): add profit dashboard page with ECharts"
```

---

### Task 7: 股票详情页嵌入 + 模型配置入口

**Files:**
- Modify: `web/templates/stock_detail.html`

**Step 1: 在股票详情页底部添加利润跟踪区块**

在 `stock_detail.html` 的主内容区末尾（研究历史区块之后），添加利润跟踪区块：

```html
<!-- 利润跟踪区块 -->
<div class="bg-white rounded-xl shadow-sm border border-gray-100 mt-6"
     x-data="profitSection()" x-init="init()">
  <div class="p-4 border-b border-gray-100 flex justify-between items-center">
    <h2 class="text-lg font-bold text-gray-900">利润跟踪</h2>
    <div class="flex gap-2">
      <button x-show="hasModel" @click="refreshData()"
              class="text-sm text-blue-600 hover:text-blue-700">刷新</button>
      <button @click="showConfig = !showConfig"
              class="text-sm text-gray-600 hover:text-gray-900"
              x-text="hasModel ? '编辑模型' : '配置模型'"></button>
    </div>
  </div>

  <!-- 有模型：图表 + 汇总表 -->
  <div x-show="hasModel && !showConfig" class="p-4">
    <div id="stock-profit-chart" style="height: 300px;"></div>
    <table class="w-full text-sm mt-4">
      <thead>
        <tr class="text-gray-500 text-xs">
          <th class="text-left py-1">场景</th>
          <th class="text-right py-1">利润</th>
          <th class="text-right py-1">价格窗口</th>
          <th class="text-right py-1">原材料价格</th>
        </tr>
      </thead>
      <tbody>
        <template x-for="s in summary" :key="s.scenario">
          <tr class="border-t border-gray-50">
            <td class="py-2 text-gray-700" x-text="s.scenario"></td>
            <td class="py-2 text-right font-medium" x-text="s.annualized_profit.toFixed(2) + ' 亿元'"></td>
            <td class="py-2 text-right text-gray-500 text-xs" x-text="s.date_range"></td>
            <td class="py-2 text-right text-gray-500 text-xs">
              <template x-for="(price, sym) in s.commodity_prices" :key="sym">
                <span class="block" x-text="sym + ': ' + price.toFixed(2)"></span>
              </template>
            </td>
          </tr>
        </template>
      </tbody>
    </table>
  </div>

  <!-- 无模型 / 配置面板 -->
  <div x-show="!hasModel || showConfig" class="p-4">
    <div class="flex gap-3 mb-4">
      <button @click="configMode = 'manual'"
              :class="configMode === 'manual' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'"
              class="px-3 py-1.5 rounded-lg text-sm">手动配置</button>
      <button @click="configMode = 'llm'"
              :class="configMode === 'llm' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-700'"
              class="px-3 py-1.5 rounded-lg text-sm">LLM 推导</button>
    </div>

    <!-- LLM 推导 -->
    <div x-show="configMode === 'llm'" class="space-y-3">
      <p class="text-sm text-gray-500">基于 Playbook 和研究历史，让 AI 自动推导利润敏感性模型。</p>
      <button @click="deriveLLM()" :disabled="deriving"
              class="bg-green-600 hover:bg-green-700 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-50">
        <span x-show="!deriving">一键推导</span>
        <span x-show="deriving">推导中...</span>
      </button>
    </div>

    <!-- 手动配置表单 -->
    <div x-show="configMode === 'manual'" class="space-y-3">
      <div>
        <label class="block text-sm text-gray-600 mb-1">年化方式</label>
        <select x-model="form.annualization" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
          <option value="H1x2">H1×2（半年报×2）</option>
          <option value="Qx4">Q×4（单季×4）</option>
          <option value="annual">年报（不乘）</option>
        </select>
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1">基准期</label>
        <input x-model="form.base_period" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
               placeholder="如 2026H1, 2026Q1">
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1">关联原材料（symbol）</label>
        <input x-model="form.commodity_symbol" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
               placeholder="如 BZ=F (Brent), HG=F (铜), GC=F (黄金), LC0 (碳酸锂)">
      </div>
      <div>
        <label class="block text-sm text-gray-600 mb-1">数据源</label>
        <select x-model="form.commodity_source" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
          <option value="yfinance">yfinance</option>
          <option value="akshare">akshare（碳酸锂等国内期货）</option>
        </select>
      </div>
      <div class="grid grid-cols-3 gap-3">
        <div>
          <label class="block text-sm text-gray-600 mb-1">基准期利润（亿元）</label>
          <input x-model.number="form.base_profit" type="number" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-sm text-gray-600 mb-1">基准期原材料均价</label>
          <input x-model.number="form.base_commodity_price" type="number" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
        </div>
        <div>
          <label class="block text-sm text-gray-600 mb-1">敏感度（亿元/单位）</label>
          <input x-model.number="form.sensitivity" type="number" step="0.1" class="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm">
        </div>
      </div>
      <button @click="saveManual()" class="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg text-sm">
        保存模型
      </button>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
function profitSection() {
  const stockId = '{{ stock_id }}';
  return {
    hasModel: false,
    showConfig: false,
    configMode: 'manual',
    deriving: false,
    summary: [],
    daily: [],
    form: {
      annualization: 'Qx4', base_period: '', commodity_symbol: '',
      commodity_source: 'yfinance', base_profit: 0, base_commodity_price: 0, sensitivity: 0,
    },

    async init() {
      const resp = await fetch(`/api/profit/${stockId}/model`);
      const model = await resp.json();
      this.hasModel = !!(model && model.stock_name);
      if (this.hasModel) await this.loadData();
    },

    async loadData() {
      const resp = await fetch(`/api/profit/${stockId}`);
      const data = await resp.json();
      this.daily = data.daily || [];
      this.summary = data.summary || [];
      this.$nextTick(() => this.renderChart());
    },

    renderChart() {
      const el = document.getElementById('stock-profit-chart');
      if (!el || !this.daily.length) return;
      const chart = echarts.init(el);
      chart.setOption({
        tooltip: { trigger: 'axis' },
        legend: { data: ['按当日价格年化利润', '年内均价年化利润'], top: 0, textStyle: { fontSize: 11 } },
        grid: { left: 60, right: 20, top: 35, bottom: 25 },
        xAxis: { type: 'category', data: this.daily.map(d => d.date.slice(5)) },
        yAxis: { type: 'value' },
        series: [
          { name: '按当日价格年化利润', type: 'line', smooth: true, symbol: 'none',
            lineStyle: { color: '#C07A3E', width: 2 }, itemStyle: { color: '#C07A3E' },
            data: this.daily.map(d => d.annualized_profit) },
          { name: '年内均价年化利润', type: 'line', smooth: true, symbol: 'none',
            lineStyle: { color: '#3B7A8A', width: 2 }, itemStyle: { color: '#3B7A8A' },
            data: this.daily.map(d => d.ytd_avg_annualized_profit) },
        ],
      });
      window.addEventListener('resize', () => chart.resize());
    },

    async refreshData() { await this.loadData(); },

    async deriveLLM() {
      this.deriving = true;
      const resp = await fetch(`/api/profit/${stockId}/model`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'llm' }),
      });
      const result = await resp.json();
      this.deriving = false;
      if (result.success) {
        this.hasModel = true;
        this.showConfig = false;
        await this.loadData();
      } else {
        alert('推导失败: ' + (result.error || '未知错误'));
      }
    },

    async saveManual() {
      const config = {
        stock_name: '{{ playbook.stock_name if playbook else stock_id }}',
        commodities: [{
          name: this.form.commodity_symbol,
          symbol: this.form.commodity_symbol,
          source: this.form.commodity_source,
          unit: '',
        }],
        annualization: this.form.annualization,
        base_period: this.form.base_period,
        parameters: {
          base_profit: this.form.base_profit,
          base_commodity_price: this.form.base_commodity_price,
          sensitivity: this.form.sensitivity,
        },
        mode: 'manual',
      };
      await fetch(`/api/profit/${stockId}/model`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      this.hasModel = true;
      this.showConfig = false;
      await this.loadData();
    },
  };
}
</script>
```

**Step 2: 运行全部测试确认无回归**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 3: Commit**

```bash
git add web/templates/stock_detail.html
git commit -m "feat(web): embed profit tracker in stock detail page"
```

---

### Task 8: 定时任务脚本 + CLAUDE.md 更新

**Files:**
- Create: `scripts/update_prices.py`
- Modify: `CLAUDE.md`

**Step 1: 创建定时任务脚本**

```python
#!/usr/bin/env python3
"""每日大宗商品价格更新脚本

用法:
    uv run python scripts/update_prices.py           # 更新所有已配置模型的品种
    uv run python scripts/update_prices.py --manual   # 同上（显式手动触发）

cron 配置示例（工作日收盘后）:
    0 22 * * 1-5 cd /path/to/investment-assistant && uv run python scripts/update_prices.py
"""

import argparse
import logging
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.storage import Storage
from core.commodity_price import CommodityPriceService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="更新大宗商品价格数据")
    parser.add_argument("--manual", action="store_true", help="手动触发标记")
    parser.parse_args()

    storage = Storage()
    price_service = CommodityPriceService()

    # 收集所有已配置利润模型的 symbol
    symbols = []
    seen = set()
    stocks_dir = storage.base_dir / "stocks"
    if stocks_dir.exists():
        for stock_dir in stocks_dir.iterdir():
            model_path = stock_dir / "profit_model.json"
            if model_path.exists():
                import json
                with open(model_path, "r", encoding="utf-8") as f:
                    model = json.load(f)
                for c in model.get("commodities", []):
                    key = (c["symbol"], c["source"])
                    if key not in seen:
                        symbols.append({"symbol": c["symbol"], "source": c["source"]})
                        seen.add(key)

    if not symbols:
        logger.info("无已配置的利润模型，跳过")
        return

    start = f"{datetime.now().year}-01-01"
    end = datetime.now().strftime("%Y-%m-%d")
    logger.info("更新 %d 个品种: %s ~ %s", len(symbols), start, end)

    price_service.refresh_all(symbols, start, end)
    logger.info("完成")


if __name__ == "__main__":
    main()
```

**Step 2: 更新 CLAUDE.md**

在 CLAUDE.md 的"常用命令"区块中添加：

```bash
# 利润跟踪
uv run python scripts/update_prices.py               # 手动更新大宗商品价格
```

在"架构"区块的依赖注入图中添加 `ProfitTracker`：

```
LLMClient + Storage
    ├── InterviewManager(client, storage)
    ├── EnvironmentCollector(client, storage)
    ├── ResearchEngine(client, storage)
    ├── PreferenceLearner(client, storage)
    └── ProfitTracker(client, storage)
```

在"最近重大变更"区块添加：

```
- 2026-03-08: 利润跟踪模块（原材料价格 → 利润敏感性）
  - 设计文档: `docs/plans/2026-03-08-profit-tracker-design.md`
```

**Step 3: 运行全部测试**

Run: `uv run python -m pytest tests/ -v --tb=short`
Expected: 全部 PASS

**Step 4: Commit**

```bash
git add scripts/update_prices.py CLAUDE.md
git commit -m "feat: add price update script and update CLAUDE.md"
```

---

## 实现顺序总结

| Task | 内容 | 依赖 |
|------|------|------|
| 1 | 添加 yfinance + akshare 依赖 | 无 |
| 2 | CommodityPriceService（SQLite 存储 + 价格采集） | Task 1 |
| 3 | ProfitModel（利润计算引擎） | 无 |
| 4 | ProfitTracker（主入口：手动配置 + LLM 推导） | Task 2, 3 |
| 5 | Web 后端 API 路由 | Task 4 |
| 6 | Dashboard 页面（ECharts） | Task 5 |
| 7 | 股票详情页嵌入 + 模型配置入口 | Task 5 |
| 8 | 定时任务脚本 + CLAUDE.md 更新 | Task 2 |

Task 1-3 可并行，Task 6-7 可并行。
