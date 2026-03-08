#!/usr/bin/env python3
"""每日大宗商品价格更新脚本

用法:
    uv run python scripts/update_prices.py           # 更新所有已配置模型的品种
    uv run python scripts/update_prices.py --manual   # 同上（显式手动触发）

cron 配置示例（工作日收盘后）:
    0 22 * * 1-5 cd /path/to/investment-assistant && uv run python scripts/update_prices.py
"""

import argparse
import json
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
