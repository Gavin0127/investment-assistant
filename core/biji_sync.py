"""Synchronization service for Biji notes and markdown export."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from markdownify import markdownify


class BijiSyncService:
    """Coordinates list scanning, detail fetching, asset download, and export."""

    def __init__(
        self,
        client,
        db,
        markdown_root: str,
        raw_root: str,
        page_size: int = 50,
    ):
        self.client = client
        self.db = db
        self.markdown_root = Path(markdown_root)
        self.raw_root = Path(raw_root)
        self.page_size = page_size
        self.markdown_root.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)

    def sync_once(self) -> dict[str, int]:
        result = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        seen_note_ids: set[str] = set()
        page = 1

        while True:
            summaries = self.client.list_notes(page=page, page_size=self.page_size)
            if not summaries:
                break

            page_note_ids = [str(item.get("note_id") or "") for item in summaries if item.get("note_id")]
            if page_note_ids and all(note_id in seen_note_ids for note_id in page_note_ids):
                break

            for summary in summaries:
                note_id = str(summary.get("note_id") or "")
                if not note_id:
                    continue
                seen_note_ids.add(note_id)

                existing = self.db.get_note(note_id)
                if not self._should_refresh_note(summary, existing):
                    result["skipped"] += 1
                    continue

                try:
                    detail = self.client.get_note_detail(note_id)
                    markdown = self._normalize_markdown(detail.get("raw_content") or "")
                    asset_records, replacements = self._download_assets(
                        note_id, detail.get("assets") or []
                    )
                    markdown = self._replace_asset_urls(markdown, replacements)
                    export_path = self._export_note_markdown(note_id, detail, markdown)
                    content_hash = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
                    now_ms = int(datetime.now().timestamp() * 1000)

                    self.db.upsert_note(
                        {
                            "note_id": note_id,
                            "title": detail.get("title") or summary.get("title") or "",
                            "summary": detail.get("summary") or summary.get("summary") or "",
                            "raw_content": detail.get("raw_content") or "",
                            "markdown_content": markdown,
                            "source_url": detail.get("source_url") or summary.get("source_url"),
                            "created_at": detail.get("created_at") or summary.get("created_at"),
                            "updated_at": detail.get("updated_at") or summary.get("updated_at"),
                            "content_hash": content_hash,
                            "missing_from_remote": 0,
                            "last_exported_at": now_ms,
                        }
                    )

                    for asset_record in asset_records:
                        self.db.upsert_asset(asset_record)

                    if existing is None:
                        result["created"] += 1
                    else:
                        result["updated"] += 1

                    self.raw_root.joinpath(f"{note_id}.json").write_text(
                        json.dumps(detail, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    if not export_path.exists():
                        raise RuntimeError("Markdown export failed")
                except Exception:
                    result["failed"] += 1

            page += 1

        return result

    @staticmethod
    def _should_refresh_note(summary: dict, existing: dict | None) -> bool:
        if existing is None:
            return True

        incoming_updated_at = summary.get("updated_at")
        existing_updated_at = existing.get("updated_at")
        if incoming_updated_at and existing_updated_at:
            return incoming_updated_at != existing_updated_at

        return not existing.get("content_hash")

    @staticmethod
    def _normalize_markdown(raw_content: str) -> str:
        if not raw_content:
            return ""

        text = raw_content.strip()
        if "<" in text and ">" in text:
            return markdownify(text, heading_style="ATX").strip()
        return text

    def _export_note_markdown(self, note_id: str, detail: dict, markdown: str) -> Path:
        note_dir = self.markdown_root / note_id
        note_dir.mkdir(parents=True, exist_ok=True)
        output_path = note_dir / "index.md"

        frontmatter = "\n".join(
            [
                "---",
                f"note_id: {json.dumps(note_id, ensure_ascii=False)}",
                f"title: {json.dumps(detail.get('title') or '', ensure_ascii=False)}",
                f"created_at: {json.dumps(detail.get('created_at') or '', ensure_ascii=False)}",
                f"updated_at: {json.dumps(detail.get('updated_at') or '', ensure_ascii=False)}",
                f"source_url: {json.dumps(detail.get('source_url') or '', ensure_ascii=False)}",
                "---",
                "",
            ]
        )
        body = markdown.strip()
        output_path.write_text(
            f"{frontmatter}{body}\n",
            encoding="utf-8",
        )
        return output_path

    def _download_assets(self, note_id: str, assets: list[dict]) -> tuple[list[dict], dict[str, str]]:
        note_dir = self.markdown_root / note_id
        assets_dir = note_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        asset_records: list[dict] = []
        replacements: dict[str, str] = {}

        for index, asset in enumerate(assets, start=1):
            asset_url = asset.get("asset_url")
            if not asset_url:
                continue

            extension = self._guess_extension(
                asset_url,
                mime_type=asset.get("mime_type"),
                asset_type=asset.get("asset_type"),
            )
            filename = f"{index:03d}{extension}"
            relative_path = f"assets/{filename}"
            destination = assets_dir / filename
            download_status = "done"
            local_path: str | None = relative_path

            try:
                self.client.download_asset(asset_url, destination)
                replacements[asset_url] = relative_path
            except Exception:
                download_status = "failed"
                local_path = None

            asset_records.append(
                {
                    "note_id": note_id,
                    "asset_url": asset_url,
                    "asset_type": asset.get("asset_type"),
                    "mime_type": asset.get("mime_type"),
                    "filename": filename,
                    "local_path": local_path,
                    "download_status": download_status,
                    "etag": asset.get("etag"),
                    "last_modified": asset.get("last_modified"),
                }
            )

        return asset_records, replacements

    @staticmethod
    def _replace_asset_urls(markdown: str, replacements: dict[str, str]) -> str:
        updated = markdown
        for remote_url, local_path in replacements.items():
            updated = updated.replace(remote_url, local_path)
        return updated

    @staticmethod
    def _guess_extension(
        asset_url: str,
        *,
        mime_type: str | None = None,
        asset_type: str | None = None,
    ) -> str:
        suffix = Path(urlparse(asset_url).path).suffix
        if suffix:
            return suffix
        if mime_type == "image/png":
            return ".png"
        if mime_type == "image/jpeg":
            return ".jpg"
        if mime_type == "audio/mpeg" or asset_type == "audio":
            return ".mp3"
        if asset_type == "image":
            return ".png"
        return ".bin"
