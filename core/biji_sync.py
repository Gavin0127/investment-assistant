"""Synchronization service for Biji notes and markdown export."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from markdownify import markdownify

from core.biji_content_parser import (
    build_display_markdown_sections,
    classify_note_content,
    slugify_note_title,
)


class BijiSyncService:
    """Coordinates list scanning, detail fetching, asset download, and export."""

    _INTERNAL_ASSET_HOST_SUFFIXES = (
        "biji.com",
        "umiwi.com",
        "igetget.com",
        "luojilab.com",
    )

    def __init__(
        self,
        client,
        db,
        markdown_root: str,
        raw_root: str,
        page_size: int = 50,
        download_images: bool = True,
    ):
        self.client = client
        self.db = db
        self.markdown_root = Path(markdown_root)
        self.raw_root = Path(raw_root)
        self.page_size = page_size
        self.download_images = download_images
        self._used_export_dirs: set[str] = set()
        self.markdown_root.mkdir(parents=True, exist_ok=True)
        self.raw_root.mkdir(parents=True, exist_ok=True)

    def sync_once(self) -> dict[str, int]:
        result = {"created": 0, "updated": 0, "skipped": 0, "failed": 0}
        self._used_export_dirs = self._load_reserved_export_dirs()
        seen_note_ids: set[str] = set()
        page = 1
        since_id = "0"
        use_cursor_scan = callable(getattr(self.client, "list_notes_batch", None))
        complete_remote_scan = False

        while True:
            summaries, page_meta = self._list_notes_page(page=page, since_id=since_id)
            if not summaries:
                complete_remote_scan = True
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
                    if existing and existing.get("missing_from_remote"):
                        self.db.upsert_note({**existing, "missing_from_remote": 0})
                    result["skipped"] += 1
                    continue

                try:
                    detail = self.client.get_note_detail(note_id)
                    normalized_raw_content = self._normalize_markdown(detail.get("raw_content") or "")
                    normalized_detail = {**detail, "raw_content": normalized_raw_content}
                    web_snapshot = self._get_note_page_snapshot(
                        note_id,
                        title=detail.get("title") or summary.get("title") or "",
                    )
                    content_parts = classify_note_content(normalized_detail, web_snapshot)
                    previous_export_dir = str((existing or {}).get("export_dir_name") or "").strip()
                    reserved_export_dirs = set(self._used_export_dirs)
                    if previous_export_dir:
                        reserved_export_dirs.discard(previous_export_dir)
                    export_dir_name = slugify_note_title(
                        detail.get("title") or summary.get("title") or "",
                        note_id=note_id,
                        existing=reserved_export_dirs,
                    )
                    if previous_export_dir:
                        self._used_export_dirs.discard(previous_export_dir)
                    self._used_export_dirs.add(export_dir_name)
                    self._cleanup_old_export_dir(existing, export_dir_name)
                    asset_records, replacements = self._download_assets(
                        export_dir_name, note_id, detail.get("assets") or []
                    )
                    markdown_body = build_display_markdown_sections(content_parts)
                    markdown_body = self._replace_asset_urls(markdown_body, replacements)
                    export_path = self._export_note_markdown(
                        export_dir_name,
                        note_id,
                        detail,
                        content_parts.get("content_mode") or "unknown",
                        markdown_body,
                    )
                    now_ms = int(datetime.now().timestamp() * 1000)
                    normalized_note = {
                        "note_id": note_id,
                        "title": detail.get("title") or summary.get("title") or "",
                        "summary": detail.get("summary") or summary.get("summary") or "",
                        "raw_content": normalized_raw_content,
                        "markdown_content": markdown_body,
                        "source_url": detail.get("source_url") or summary.get("source_url"),
                        "created_at": detail.get("created_at") or summary.get("created_at"),
                        "updated_at": detail.get("updated_at") or summary.get("updated_at"),
                        "content_hash": hashlib.sha256(markdown_body.encode("utf-8")).hexdigest(),
                        "missing_from_remote": 0,
                        "last_exported_at": now_ms,
                        "content_mode": content_parts.get("content_mode") or "unknown",
                        "original_content": content_parts.get("original_content") or "",
                        "ai_summary_content": content_parts.get("ai_summary_content") or "",
                        "display_content": content_parts.get("display_content") or "",
                        "content_source": content_parts.get("content_source") or "api_detail",
                        "export_dir_name": export_dir_name,
                    }
                    self._write_raw_snapshot(note_id, detail, web_snapshot, normalized_note)

                    self.db.upsert_note(
                        normalized_note
                    )

                    for asset_record in asset_records:
                        self.db.upsert_asset(asset_record)

                    if existing is None:
                        result["created"] += 1
                    else:
                        result["updated"] += 1

                    if not export_path.exists():
                        raise RuntimeError("Markdown export failed")
                except Exception:
                    result["failed"] += 1

            if page_meta.get("has_more") is False:
                complete_remote_scan = True
                break

            total_items = page_meta.get("total_items")
            if isinstance(total_items, int) and len(seen_note_ids) >= total_items:
                complete_remote_scan = True
                break

            if use_cursor_scan:
                last_note_id = page_note_ids[-1] if page_note_ids else ""
                if not last_note_id or last_note_id == since_id:
                    break
                since_id = last_note_id
            else:
                page += 1

        if complete_remote_scan:
            self._mark_missing_notes(seen_note_ids)

        return result

    def _list_notes_page(self, page: int, since_id: str = "0") -> tuple[list[dict], dict]:
        list_notes_batch = getattr(self.client, "list_notes_batch", None)
        if callable(list_notes_batch):
            return list_notes_batch(since_id=since_id, limit=self.page_size, sort="edit_desc")
        list_notes_page = getattr(self.client, "list_notes_page", None)
        if callable(list_notes_page):
            return list_notes_page(page=page, page_size=self.page_size)
        return self.client.list_notes(page=page, page_size=self.page_size), {}

    @staticmethod
    def _should_refresh_note(summary: dict, existing: dict | None) -> bool:
        if existing is None:
            return True
        if existing.get("missing_from_remote"):
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

    def _export_note_markdown(
        self,
        export_dir_name: str,
        note_id: str,
        detail: dict,
        content_mode: str,
        markdown: str,
    ) -> Path:
        note_dir = self.markdown_root / export_dir_name
        note_dir.mkdir(parents=True, exist_ok=True)
        output_path = note_dir / "index.md"

        frontmatter = "\n".join(
            [
                "---",
                f"note_id: {json.dumps(note_id, ensure_ascii=False)}",
                f"title: {json.dumps(detail.get('title') or '', ensure_ascii=False)}",
                f"content_mode: {json.dumps(content_mode, ensure_ascii=False)}",
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

    def _write_raw_snapshot(
        self,
        note_id: str,
        detail: dict,
        web_snapshot: dict | None,
        normalized_note: dict,
    ) -> Path:
        snapshot_path = self.raw_root / f"{note_id}.json"
        snapshot_path.write_text(
            json.dumps(
                {
                    "api_detail": detail,
                    "web_snapshot": web_snapshot,
                    "normalized_note": normalized_note,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return snapshot_path

    def _download_assets(
        self,
        export_dir_name: str,
        note_id: str,
        assets: list[dict],
    ) -> tuple[list[dict], dict[str, str]]:
        note_dir = self.markdown_root / export_dir_name
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
            is_image = (
                asset.get("asset_type") == "image"
                or str(asset.get("mime_type") or "").startswith("image/")
            )

            if self._is_external_asset_url(asset_url):
                download_status = "external"
                local_path = None
            elif is_image and not self.download_images:
                download_status = "skipped"
                local_path = None
            else:
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

    def _get_note_page_snapshot(self, note_id: str, *, title: str = "") -> dict | None:
        get_snapshot = getattr(self.client, "get_note_page_snapshot", None)
        if not callable(get_snapshot):
            return None
        try:
            snapshot = get_snapshot(note_id)
        except Exception:
            return None
        return self._normalize_web_snapshot(snapshot, title=title)

    @classmethod
    def _normalize_web_snapshot(cls, web_snapshot: dict | None, *, title: str = "") -> dict | None:
        if web_snapshot is None:
            return None

        normalized = dict(web_snapshot)
        existing_sections = normalized.get("raw_sections") or {}
        if existing_sections:
            normalized["raw_sections"] = existing_sections
            return normalized

        html = str(normalized.get("html") or "").strip()
        html_sections = cls._extract_sections_from_html(html)
        if html_sections:
            normalized["raw_sections"] = html_sections
            return normalized

        text = str(normalized.get("text") or "").strip()
        if not text:
            normalized["raw_sections"] = {}
            return normalized

        native_content = cls._extract_native_content_from_text(text, title=title)
        normalized["raw_sections"] = {"native_content": native_content} if native_content else {}
        return normalized

    @staticmethod
    def _extract_sections_from_html(html: str) -> dict[str, str]:
        if not html:
            return {}

        pattern = re.compile(
            r"<h[1-6][^>]*>\s*原始内容\s*</h[1-6]>\s*(.*?)\s*"
            r"<h[1-6][^>]*>\s*(?:AI 总结|智能总结)\s*</h[1-6]>\s*(.*)",
            re.DOTALL | re.IGNORECASE,
        )
        match = pattern.search(html)
        if not match:
            return {}
        return {
            "original_content": markdownify(match.group(1), heading_style="ATX").strip(),
            "ai_summary_content": markdownify(match.group(2), heading_style="ATX").strip(),
        }

    @staticmethod
    def _extract_native_content_from_text(text: str, *, title: str = "") -> str:
        noise_lines = {
            "首页",
            "AI助手",
            "知识库",
            "标签",
            "下载App",
            "Get达人",
            "返回上一页",
            "安装小龙虾技能",
            "让 AI 助手帮你记笔记，对话即可完成。",
            "重新加载",
            "当前网页无法显示",
            "resource not exist",
        }
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line and line not in noise_lines]
        if title:
            try:
                title_index = next(index for index, line in enumerate(lines) if line == title)
                lines = lines[title_index + 1 :]
            except StopIteration:
                pass

        filtered_lines = [line for line in lines if line]
        if not filtered_lines:
            return ""

        joined = "\n".join(filtered_lines)
        if any(marker in joined for marker in ("当前网页无法显示", "resource not exist")):
            return ""

        return "\n\n".join(filtered_lines).strip()

    def _load_reserved_export_dirs(self) -> set[str]:
        reserved: set[str] = set()
        for note in self.db.list_notes():
            export_dir_name = str(note.get("export_dir_name") or "").strip()
            if export_dir_name:
                reserved.add(export_dir_name)
        return reserved

    @staticmethod
    def _replace_asset_urls(markdown: str, replacements: dict[str, str]) -> str:
        updated = markdown
        for remote_url, local_path in replacements.items():
            updated = updated.replace(remote_url, local_path)
        return updated

    @classmethod
    def _is_external_asset_url(cls, asset_url: str) -> bool:
        hostname = (urlparse(asset_url).hostname or "").lower()
        if not hostname:
            return False
        return not any(
            hostname == suffix or hostname.endswith(f".{suffix}")
            for suffix in cls._INTERNAL_ASSET_HOST_SUFFIXES
        )

    def _mark_missing_notes(self, seen_note_ids: set[str]) -> None:
        for note in self.db.list_notes():
            note_id = str(note.get("note_id") or "")
            if not note_id or note_id in seen_note_ids:
                continue
            self.db.upsert_note({**note, "missing_from_remote": 1})

    def _cleanup_old_export_dir(self, existing: dict | None, export_dir_name: str) -> None:
        previous_dir_name = str((existing or {}).get("export_dir_name") or "").strip()
        if not previous_dir_name or previous_dir_name == export_dir_name:
            return
        previous_dir = self.markdown_root / previous_dir_name
        if previous_dir.exists():
            shutil.rmtree(previous_dir)

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
