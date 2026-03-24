#!/usr/bin/env python3
"""Biji note synchronization CLI."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.biji_client import BijiClient
from core.biji_db import BijiDB
from core.biji_sync import BijiSyncService
from core.storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="同步 Biji 笔记到本地")
    parser.add_argument("--base-dir", default=None, help="本地数据目录，默认 ~/.investment-assistant")
    parser.add_argument("--page-size", type=int, default=None, help="每页抓取数量")
    parser.add_argument("--full", action="store_true", help="预留的全量同步开关")
    parser.add_argument("--no-images", action="store_true", help="跳过图片下载，保留远程 URL")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细日志")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    try:
        storage = Storage(base_dir=args.base_dir)
        biji_config = storage.get_biji_config()
        token = storage.get_biji_token()
        if not token:
            print("Missing Biji bearer token in local config", file=sys.stderr)
            return 1

        data_dir = storage.base_dir / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "biji_notes.db"
        markdown_root = data_dir / "biji_markdown"
        raw_root = data_dir / "biji_raw"
        page_size = (
            args.page_size
            if args.page_size is not None
            else (biji_config.get("page_size") or 50)
        )
        download_images = bool(biji_config.get("download_images", True)) and not args.no_images

        client = BijiClient(
            api_base=biji_config["api_base"],
            bearer_token=token,
        )

        db = BijiDB(str(db_path))
        service = BijiSyncService(
            client=client,
            db=db,
            markdown_root=str(markdown_root),
            raw_root=str(raw_root),
            page_size=page_size,
            download_images=download_images,
        )

        result = service.sync_once()
        print(
            "created={created} updated={updated} skipped={skipped} failed={failed}".format(
                **result
            )
        )
        return 0
    except Exception as exc:
        print(f"Biji sync failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
