#!/usr/bin/env python3
"""雪球内容同步 CLI"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.xueqiu_scraper import XueqiuScraper

_DEFAULT_BASE = os.path.expanduser("~/.investment-assistant/data")


def main():
    parser = argparse.ArgumentParser(description="同步雪球用户动态")
    parser.add_argument("--user-id", type=int, required=True, help="雪球用户 ID")
    parser.add_argument("--headless", action="store_true", help="无头模式（调试用）")
    parser.add_argument("--db-path", default=os.path.join(_DEFAULT_BASE, "xueqiu_posts.db"))
    parser.add_argument("--image-dir", default=os.path.join(_DEFAULT_BASE, "xueqiu_images"))
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    scraper = XueqiuScraper(args.db_path, args.image_dir)
    scraper.login_and_sync(args.user_id, headless=args.headless)


if __name__ == "__main__":
    main()
