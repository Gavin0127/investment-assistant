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
                cached = self.price_service.fetch_daily(c["symbol"], c["source"], start, end)
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
