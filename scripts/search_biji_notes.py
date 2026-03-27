#!/usr/bin/env python3
"""Search local hybrid retrieval indexes for Biji notes."""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.biji_db import BijiDB
from core.biji_search import BijiHybridSearchService
from core.biji_vector_store import BijiVectorStore, OpenAIEmbedder
from core.storage import Storage


class _NullVectorStore:
    def search(self, query, top_k=10):
        return []


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="搜索 Biji 本地混合检索索引")
    parser.add_argument("query", help="自然语言查询")
    parser.add_argument("--base-dir", default=None, help="本地数据目录，默认 ~/.investment-assistant")
    parser.add_argument("--top-k", type=int, default=None, help="返回结果数量")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        storage = Storage(base_dir=args.base_dir)
        cfg = storage.get_biji_retrieval_config()
        data_dir = storage.base_dir / "data"
        top_k = args.top_k if args.top_k is not None else cfg["top_k"]
        if cfg["embedding_provider"] != "openai":
            raise RuntimeError(f"Unsupported embedding provider: {cfg['embedding_provider']}")

        try:
            vector_store = BijiVectorStore(
                db_dir=cfg["vector_db_dir"],
                embedder=OpenAIEmbedder(
                    model=cfg["embedding_model"],
                    api_key=storage.get_api_key(),
                    base_url=storage.get_llm_base_url(),
                ),
            )
        except Exception:
            vector_store = _NullVectorStore()

        service = BijiHybridSearchService(
            db=BijiDB(str(data_dir / "biji_notes.db")),
            vector_store=vector_store,
        )
        result = service.search(args.query, top_k=top_k)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f"Biji search failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
