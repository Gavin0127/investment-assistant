"""SQLite data layer for Biji notes."""

import contextlib
import sqlite3
from pathlib import Path
from typing import Optional


class BijiDB:
    """SQLite storage for Biji notes, assets, and sync state."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextlib.contextmanager
    def _get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    def _init_schema(self):
        with self._get_conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    note_id TEXT PRIMARY KEY,
                    title TEXT,
                    summary TEXT,
                    raw_content TEXT,
                    markdown_content TEXT,
                    source_url TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
                    content_hash TEXT,
                    missing_from_remote INTEGER NOT NULL DEFAULT 0,
                    last_exported_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS note_assets (
                    note_id TEXT NOT NULL,
                    asset_url TEXT NOT NULL,
                    asset_type TEXT,
                    mime_type TEXT,
                    filename TEXT,
                    local_path TEXT,
                    download_status TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
                    PRIMARY KEY (note_id, asset_url),
                    FOREIGN KEY (note_id) REFERENCES notes(note_id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS sync_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
                );

                CREATE TABLE IF NOT EXISTS api_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    entity_id TEXT,
                    payload TEXT NOT NULL,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000)
                );
                """
            )
            conn.commit()

    def upsert_note(self, note: dict):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, title, summary, raw_content, markdown_content,
                    source_url, created_at, updated_at, content_hash,
                    missing_from_remote, last_exported_at
                ) VALUES (
                    :note_id, :title, :summary, :raw_content, :markdown_content,
                    :source_url, :created_at, :updated_at, :content_hash,
                    :missing_from_remote, :last_exported_at
                )
                ON CONFLICT(note_id) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    raw_content = excluded.raw_content,
                    markdown_content = excluded.markdown_content,
                    source_url = excluded.source_url,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash,
                    missing_from_remote = excluded.missing_from_remote,
                    last_exported_at = excluded.last_exported_at,
                    saved_at = strftime('%s', 'now') * 1000
                """,
                {
                    "note_id": note["note_id"],
                    "title": note.get("title"),
                    "summary": note.get("summary"),
                    "raw_content": note.get("raw_content"),
                    "markdown_content": note.get("markdown_content"),
                    "source_url": note.get("source_url"),
                    "created_at": note.get("created_at"),
                    "updated_at": note.get("updated_at"),
                    "content_hash": note.get("content_hash"),
                    "missing_from_remote": int(bool(note.get("missing_from_remote", 0))),
                    "last_exported_at": note.get("last_exported_at"),
                },
            )
            conn.commit()

    def get_note(self, note_id: str) -> Optional[dict]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE note_id = ?",
                (note_id,),
            ).fetchone()
            return dict(row) if row is not None else None

    def list_notes(self) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY updated_at DESC, note_id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def upsert_asset(self, asset: dict):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO note_assets (
                    note_id, asset_url, asset_type, mime_type, filename,
                    local_path, download_status, etag, last_modified
                ) VALUES (
                    :note_id, :asset_url, :asset_type, :mime_type, :filename,
                    :local_path, :download_status, :etag, :last_modified
                )
                ON CONFLICT(note_id, asset_url) DO UPDATE SET
                    asset_type = excluded.asset_type,
                    mime_type = excluded.mime_type,
                    filename = excluded.filename,
                    local_path = excluded.local_path,
                    download_status = excluded.download_status,
                    etag = excluded.etag,
                    last_modified = excluded.last_modified,
                    saved_at = strftime('%s', 'now') * 1000
                """,
                {
                    "note_id": asset["note_id"],
                    "asset_url": asset["asset_url"],
                    "asset_type": asset.get("asset_type"),
                    "mime_type": asset.get("mime_type"),
                    "filename": asset.get("filename"),
                    "local_path": asset.get("local_path"),
                    "download_status": asset.get("download_status"),
                    "etag": asset.get("etag"),
                    "last_modified": asset.get("last_modified"),
                },
            )
            conn.commit()

    def list_assets(self, note_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM note_assets
                WHERE note_id = ?
                ORDER BY saved_at ASC, asset_url ASC
                """,
                (note_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_sync_state(self, key: str, value: str):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO sync_state (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = strftime('%s', 'now') * 1000
                """,
                (key, value),
            )
            conn.commit()

    def get_sync_state(self, key: str) -> Optional[str]:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM sync_state WHERE key = ?",
                (key,),
            ).fetchone()
            return row["value"] if row is not None else None
