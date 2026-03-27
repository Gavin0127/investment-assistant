"""SQLite data layer for Biji notes."""

import contextlib
import sqlite3
from pathlib import Path
from typing import Optional


class BijiDB:
    """SQLite storage for Biji notes, assets, and sync state."""

    _NOTE_COLUMNS = {
        "content_mode": "TEXT",
        "original_content": "TEXT",
        "ai_summary_content": "TEXT",
        "display_content": "TEXT",
        "content_source": "TEXT",
        "export_dir_name": "TEXT",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._fts_match_enabled = True
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def _to_int_bool(value) -> int:
        if value is None:
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"", "0", "false", "no", "off"}:
                return 0
            if normalized in {"1", "true", "yes", "on"}:
                return 1
        return int(bool(value))

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
                    content_mode TEXT,
                    original_content TEXT,
                    ai_summary_content TEXT,
                    display_content TEXT,
                    content_source TEXT,
                    raw_content TEXT,
                    markdown_content TEXT,
                    source_url TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
                    content_hash TEXT,
                    missing_from_remote INTEGER NOT NULL DEFAULT 0,
                    last_exported_at INTEGER,
                    export_dir_name TEXT
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

                CREATE TABLE IF NOT EXISTS note_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    note_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    section_type TEXT NOT NULL,
                    text TEXT NOT NULL,
                    token_estimate INTEGER,
                    char_start INTEGER,
                    char_end INTEGER,
                    content_hash TEXT NOT NULL,
                    markdown_path TEXT NOT NULL,
                    saved_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now') * 1000),
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
            self._ensure_notes_fts(conn)
            self._ensure_note_columns(conn)
            self._backfill_notes_fts(conn)
            conn.commit()

    def _ensure_note_columns(self, conn):
        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(notes)").fetchall()
        }
        for column, column_type in self._NOTE_COLUMNS.items():
            if column in existing_columns:
                continue
            conn.execute(f"ALTER TABLE notes ADD COLUMN {column} {column_type}")

    def _ensure_notes_fts(self, conn):
        create_trigram = """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                note_id UNINDEXED,
                title,
                summary,
                original_content,
                ai_summary_content,
                display_content,
                tokenize='trigram'
            );
        """
        create_default = """
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                note_id UNINDEXED,
                title,
                summary,
                original_content,
                ai_summary_content,
                display_content
            );
        """
        create_plain = """
            CREATE TABLE IF NOT EXISTS notes_fts (
                note_id TEXT PRIMARY KEY,
                title TEXT,
                summary TEXT,
                original_content TEXT,
                ai_summary_content TEXT,
                display_content TEXT
            );
        """
        try:
            conn.execute(create_trigram)
            self._fts_match_enabled = True
            return
        except sqlite3.OperationalError:
            pass

        try:
            conn.execute(create_default)
            self._fts_match_enabled = True
            return
        except sqlite3.OperationalError:
            self._fts_match_enabled = False
            self._recreate_plain_notes_fts(conn)

    def _backfill_notes_fts(self, conn):
        rows = conn.execute(
            """
            SELECT note_id, title, summary, original_content, ai_summary_content, display_content
            FROM notes
            """
        ).fetchall()
        try:
            conn.execute("DELETE FROM notes_fts")
        except sqlite3.OperationalError:
            self._fts_match_enabled = False
            self._recreate_plain_notes_fts(conn)
        for row in rows:
            self._sync_note_fts(conn, dict(row))

    @staticmethod
    def _recreate_plain_notes_fts(conn):
        conn.execute("DROP TABLE IF EXISTS notes_fts")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notes_fts (
                note_id TEXT PRIMARY KEY,
                title TEXT,
                summary TEXT,
                original_content TEXT,
                ai_summary_content TEXT,
                display_content TEXT
            );
            """
        )

    def upsert_note(self, note: dict):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO notes (
                    note_id, title, summary, content_mode, original_content,
                    ai_summary_content, display_content, content_source,
                    raw_content, markdown_content,
                    source_url, created_at, updated_at, content_hash,
                    missing_from_remote, last_exported_at, export_dir_name
                ) VALUES (
                    :note_id, :title, :summary, :content_mode, :original_content,
                    :ai_summary_content, :display_content, :content_source,
                    :raw_content, :markdown_content,
                    :source_url, :created_at, :updated_at, :content_hash,
                    :missing_from_remote, :last_exported_at, :export_dir_name
                )
                ON CONFLICT(note_id) DO UPDATE SET
                    title = excluded.title,
                    summary = excluded.summary,
                    content_mode = excluded.content_mode,
                    original_content = excluded.original_content,
                    ai_summary_content = excluded.ai_summary_content,
                    display_content = excluded.display_content,
                    content_source = excluded.content_source,
                    raw_content = excluded.raw_content,
                    markdown_content = excluded.markdown_content,
                    source_url = excluded.source_url,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at,
                    content_hash = excluded.content_hash,
                    missing_from_remote = excluded.missing_from_remote,
                    last_exported_at = excluded.last_exported_at,
                    export_dir_name = excluded.export_dir_name,
                    saved_at = strftime('%s', 'now') * 1000
                """,
                {
                    "note_id": note["note_id"],
                    "title": note.get("title"),
                    "summary": note.get("summary"),
                    "content_mode": note.get("content_mode"),
                    "original_content": note.get("original_content"),
                    "ai_summary_content": note.get("ai_summary_content"),
                    "display_content": note.get("display_content"),
                    "content_source": note.get("content_source"),
                    "raw_content": note.get("raw_content"),
                    "markdown_content": note.get("markdown_content"),
                    "source_url": note.get("source_url"),
                    "created_at": note.get("created_at"),
                    "updated_at": note.get("updated_at"),
                    "content_hash": note.get("content_hash"),
                    "missing_from_remote": self._to_int_bool(
                        note.get("missing_from_remote", 0)
                    ),
                    "last_exported_at": note.get("last_exported_at"),
                    "export_dir_name": note.get("export_dir_name"),
                },
            )
            self._sync_note_fts(conn, note)
            conn.commit()

    def _sync_note_fts(self, conn, note: dict):
        try:
            conn.execute("DELETE FROM notes_fts WHERE note_id = ?", (note["note_id"],))
            conn.execute(
                """
                INSERT INTO notes_fts (
                    note_id, title, summary, original_content, ai_summary_content, display_content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note["note_id"],
                    note.get("title") or "",
                    note.get("summary") or "",
                    note.get("original_content") or "",
                    note.get("ai_summary_content") or "",
                    note.get("display_content") or "",
                ),
            )
        except sqlite3.OperationalError:
            self._fts_match_enabled = False
            BijiDB._recreate_plain_notes_fts(conn)
            conn.execute(
                """
                INSERT INTO notes_fts (
                    note_id, title, summary, original_content, ai_summary_content, display_content
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note["note_id"],
                    note.get("title") or "",
                    note.get("summary") or "",
                    note.get("original_content") or "",
                    note.get("ai_summary_content") or "",
                    note.get("display_content") or "",
                ),
            )

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

    def upsert_chunk(self, chunk: dict):
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO note_chunks (
                    chunk_id, note_id, chunk_index, section_type, text,
                    token_estimate, char_start, char_end, content_hash, markdown_path
                ) VALUES (
                    :chunk_id, :note_id, :chunk_index, :section_type, :text,
                    :token_estimate, :char_start, :char_end, :content_hash, :markdown_path
                )
                ON CONFLICT(chunk_id) DO UPDATE SET
                    note_id = excluded.note_id,
                    chunk_index = excluded.chunk_index,
                    section_type = excluded.section_type,
                    text = excluded.text,
                    token_estimate = excluded.token_estimate,
                    char_start = excluded.char_start,
                    char_end = excluded.char_end,
                    content_hash = excluded.content_hash,
                    markdown_path = excluded.markdown_path,
                    saved_at = strftime('%s', 'now') * 1000
                """,
                {
                    "chunk_id": chunk["chunk_id"],
                    "note_id": chunk["note_id"],
                    "chunk_index": chunk["chunk_index"],
                    "section_type": chunk["section_type"],
                    "text": chunk["text"],
                    "token_estimate": chunk.get("token_estimate"),
                    "char_start": chunk.get("char_start"),
                    "char_end": chunk.get("char_end"),
                    "content_hash": chunk["content_hash"],
                    "markdown_path": chunk["markdown_path"],
                },
            )
            conn.commit()

    def replace_chunks_for_note(self, note_id: str, chunks: list[dict]):
        for chunk in chunks:
            if chunk["note_id"] != note_id:
                raise ValueError("Chunk note_id mismatch")
        with self._get_conn() as conn:
            conn.execute("DELETE FROM note_chunks WHERE note_id = ?", (note_id,))
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT INTO note_chunks (
                        chunk_id, note_id, chunk_index, section_type, text,
                        token_estimate, char_start, char_end, content_hash, markdown_path
                    ) VALUES (
                        :chunk_id, :note_id, :chunk_index, :section_type, :text,
                        :token_estimate, :char_start, :char_end, :content_hash, :markdown_path
                    )
                    """,
                    {
                        "chunk_id": chunk["chunk_id"],
                        "note_id": chunk["note_id"],
                        "chunk_index": chunk["chunk_index"],
                        "section_type": chunk["section_type"],
                        "text": chunk["text"],
                        "token_estimate": chunk.get("token_estimate"),
                        "char_start": chunk.get("char_start"),
                        "char_end": chunk.get("char_end"),
                        "content_hash": chunk["content_hash"],
                        "markdown_path": chunk["markdown_path"],
                    },
                )
            conn.commit()

    def list_chunks(self, note_id: str) -> list[dict]:
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT * FROM note_chunks
                WHERE note_id = ?
                ORDER BY chunk_index ASC, chunk_id ASC
                """,
                (note_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def delete_chunks_not_in(self, note_ids: set[str]) -> None:
        with self._get_conn() as conn:
            if note_ids:
                placeholders = ",".join("?" for _ in note_ids)
                conn.execute(
                    f"DELETE FROM note_chunks WHERE note_id NOT IN ({placeholders})",
                    tuple(note_ids),
                )
            else:
                conn.execute("DELETE FROM note_chunks")
            conn.commit()

    def search_notes_fts(self, query: str) -> list[dict]:
        query = str(query or "").strip()
        if not query:
            return []
        with self._get_conn() as conn:
            if self._fts_match_enabled:
                try:
                    literal_query = '"' + query.replace('"', '""') + '"'
                    rows = conn.execute(
                        """
                        SELECT n.*
                        FROM notes_fts f
                        JOIN notes n ON n.note_id = f.note_id
                        WHERE notes_fts MATCH ?
                        ORDER BY bm25(notes_fts), n.updated_at DESC, n.note_id DESC
                        """,
                        (literal_query,),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = conn.execute(
                        """
                        SELECT DISTINCT n.*
                        FROM notes n
                        WHERE n.title LIKE ?
                           OR n.summary LIKE ?
                           OR n.original_content LIKE ?
                           OR n.ai_summary_content LIKE ?
                           OR n.display_content LIKE ?
                        ORDER BY n.updated_at DESC, n.note_id DESC
                        """,
                        tuple([f"%{query}%"] * 5),
                    ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT DISTINCT n.*
                    FROM notes n
                    WHERE n.title LIKE ?
                       OR n.summary LIKE ?
                       OR n.original_content LIKE ?
                       OR n.ai_summary_content LIKE ?
                       OR n.display_content LIKE ?
                    ORDER BY n.updated_at DESC, n.note_id DESC
                    """,
                    tuple([f"%{query}%"] * 5),
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
                ORDER BY filename ASC, asset_url ASC
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
