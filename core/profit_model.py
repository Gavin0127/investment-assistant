"""利润计算引擎"""

from __future__ import annotations

import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

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
        base_profit = self.params["base_profit"]
        base_price = self.params["base_commodity_price"]
        sensitivity = self.params["sensitivity"]
        return base_profit + (commodity_price - base_price) * sensitivity

    def _period_profit_multi(self, prices_by_symbol: Dict[str, float]) -> float:
        total = 0.0
        for commodity in self.commodities:
            name_key = self._commodity_param_key(commodity)
            sub = self.params.get(name_key, {})
            if not sub:
                continue
            price = prices_by_symbol.get(commodity["symbol"], sub.get("base_price", 0))
            total += sub["base_profit"] + (price - sub["base_price"]) * sub["sensitivity"]
        return total

    def _commodity_param_key(self, commodity: Dict) -> str:
        """从 commodities 条目推导 parameters 中的 key

        匹配策略：精确匹配 → 模糊匹配(带警告) → symbol 前缀 → 按位置索引兜底
        """
        name = commodity["name"].lower()
        sym = commodity["symbol"].lower().replace("=", "").replace(".", "")

        # 1) 精确匹配：key == name 或 key == symbol 前缀
        for key in self.params:
            kl = key.lower()
            if kl == name or kl == sym:
                return key

        # 2) 模糊匹配：子串包含（加日志警告）
        for key in self.params:
            kl = key.lower()
            if kl in name or name in kl:
                logger.warning(
                    "commodity %r 通过模糊匹配映射到参数 key %r（非精确匹配）",
                    commodity["name"], key,
                )
                return key

        # 3) 按位置索引兜底
        param_keys = list(self.params.keys())
        for i, c in enumerate(self.commodities):
            if c["symbol"] == commodity["symbol"] and i < len(param_keys):
                return param_keys[i]
        return sym

    def calculate(self, prices: Dict[str, List[Dict]]) -> List[Dict]:
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
        primary = self.commodities[0]["symbol"]
        primary_rows = prices.get(primary, [])
        price_index: Dict[str, Dict[str, float]] = {}
        running_sums: Dict[str, float] = {}
        running_counts: Dict[str, int] = {}
        last_known: Dict[str, float] = {}  # forward fill 用
        for c in self.commodities:
            sym = c["symbol"]
            price_index[sym] = {r["date"]: r["close"] for r in prices.get(sym, [])}
            running_sums[sym] = 0.0
            running_counts[sym] = 0
            sub = self.params.get(self._commodity_param_key(c), {})
            last_known[sym] = sub.get("base_price", 0)  # 初始值为 base_price

        result = []
        for row in primary_rows:
            d = row["date"]
            day_prices = {}
            avg_prices = {}
            for c in self.commodities:
                sym = c["symbol"]
                p = price_index[sym].get(d)
                if p is not None:
                    last_known[sym] = p  # 更新 forward fill 值
                else:
                    p = last_known[sym]  # forward fill：用最近已知价格
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
        daily = self.calculate(prices)
        if not daily:
            return []

        latest = daily[-1]

        # 按日期过滤最近1个自然月（而非固定30条）
        latest_date = latest["date"]
        one_month_ago = latest_date[:8] + "01"  # 当月1号作为近似
        if int(latest_date[5:7]) > 1:
            month = int(latest_date[5:7]) - 1
            one_month_ago = f"{latest_date[:5]}{month:02d}-{latest_date[8:]}"
        else:
            one_month_ago = f"{int(latest_date[:4]) - 1}-12-{latest_date[8:]}"
        last_month = [d for d in daily if d["date"] >= one_month_ago]
        if not last_month:
            last_month = daily[-30:] if len(daily) >= 30 else daily

        avg_profit_1m = sum(d["annualized_profit"] for d in last_month) / len(last_month)
        ytd_profit = daily[-1]["ytd_avg_annualized_profit"]

        def avg_commodity_prices(rows):
            if not rows:
                return {}
            out = {}
            for sym in rows[0].get("commodity_prices", {}):
                vals = [r["commodity_prices"].get(sym, 0) for r in rows]
                out[sym] = round(sum(vals) / len(vals), 2)
            return out

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
                "commodity_prices": avg_commodity_prices(last_month),
                "date_range": f"{last_month[0]['date']} 至 {last_month[-1]['date']}",
            },
            {
                "scenario": "年初至今均价年化",
                "annualized_profit": ytd_profit,
                "commodity_prices": avg_commodity_prices(daily),
                "date_range": f"{daily[0]['date']} 至 {daily[-1]['date']}",
            },
        ]
