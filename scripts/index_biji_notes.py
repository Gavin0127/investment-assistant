#!/usr/bin/env python3
"""Build local hybrid retrieval indexes for Biji notes."""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.biji_db import BijiDB
from core.biji_index_builder import BijiIndexBuilder
from core.biji_vector_store import BijiVectorStore, OpenAIEmbedder
from core.storage import Storage


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="构建 Biji 本地混合检索索引")
    parser.add_argument("--base-dir", default=None, help="本地数据目录，默认 ~/.investment-assistant")
    parser.add_argument("--rebuild", action="store_true", help="清空已有 chunk 与向量索引后重建")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        storage = Storage(base_dir=args.base_dir)
        cfg = storage.get_biji_retrieval_config()
        data_dir = storage.base_dir / "data"
        db = BijiDB(str(data_dir / "biji_notes.db"))
        if cfg["embedding_provider"] != "openai":
            raise RuntimeError(f"Unsupported embedding provider: {cfg['embedding_provider']}")
        indexer = BijiIndexBuilder(
            db=db,
            vector_store=BijiVectorStore(
                db_dir=cfg["vector_db_dir"],
                embedder=OpenAIEmbedder(model=cfg["embedding_model"]),
            ),
            markdown_root=str(data_dir / "biji_markdown"),
            vector_db_dir=cfg["vector_db_dir"],
            chunk_size=cfg["chunk_size"],
            chunk_overlap=cfg["chunk_overlap"],
        )
        result = indexer.rebuild(full_rebuild=args.rebuild)
        print(
            "notes_indexed={notes_indexed} chunks_indexed={chunks_indexed}".format(
                **result
            )
        )
        return 0
    except Exception as exc:
        print(f"Biji index build failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
