"""SQLite import history and content-hash duplicate detection."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from photo_importer.selection import ImportedIndex

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_serial   TEXT,
    camera_name     TEXT,
    filename        TEXT NOT NULL,
    source_filename TEXT,
    capture_time    TEXT,
    sha256          TEXT NOT NULL,
    destination     TEXT NOT NULL,
    file_size       INTEGER,
    session_id      TEXT,
    import_time     TEXT NOT NULL,
    UNIQUE(sha256)
);

CREATE INDEX IF NOT EXISTS idx_imports_filename ON imports(filename);
CREATE INDEX IF NOT EXISTS idx_imports_session ON imports(session_id);
CREATE INDEX IF NOT EXISTS idx_imports_time ON imports(import_time);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    camera_name     TEXT,
    camera_serial   TEXT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    files_imported  INTEGER DEFAULT 0,
    files_skipped   INTEGER DEFAULT 0,
    status          TEXT NOT NULL,
    error_message   TEXT
);

CREATE INDEX IF NOT EXISTS idx_imports_name_size ON imports(filename, file_size);
CREATE INDEX IF NOT EXISTS idx_imports_source_name ON imports(source_filename, file_size);
"""


@dataclass
class ImportRecord:
    camera_serial: str | None
    camera_name: str | None
    filename: str
    capture_time: str | None
    sha256: str
    destination: str
    file_size: int | None
    session_id: str | None
    import_time: str
    # Name the file had on the camera, which is what listings are matched against.
    source_filename: str | None = None


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self._connect()
            try:
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def _init_schema(self) -> None:
        with self.connection() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Add columns introduced after a database may already have been created."""
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(imports)")}
        if "source_filename" not in columns:
            connection.execute("ALTER TABLE imports ADD COLUMN source_filename TEXT")

    def has_hash(self, sha256: str) -> bool:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM imports WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
            return row is not None

    def record_import(self, record: ImportRecord) -> bool:
        """Insert an import record; return False when its hash already exists."""
        with self.connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO imports (
                        camera_serial, camera_name, filename, source_filename,
                        capture_time, sha256, destination, file_size,
                        session_id, import_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.camera_serial,
                        record.camera_name,
                        record.filename,
                        record.source_filename or record.filename,
                        record.capture_time,
                        record.sha256,
                        record.destination,
                        record.file_size,
                        record.session_id,
                        record.import_time,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def start_session(
        self,
        session_id: str,
        camera_name: str | None,
        camera_serial: str | None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO sessions
                    (id, camera_name, camera_serial, started_at, status)
                VALUES (?, ?, ?, ?, 'running')
                """,
                (session_id, camera_name, camera_serial, datetime.now(UTC).isoformat()),
            )

    def finish_session(
        self,
        session_id: str,
        *,
        files_imported: int,
        files_skipped: int,
        status: str = "success",
        error_message: str | None = None,
    ) -> None:
        with self.connection() as connection:
            connection.execute(
                """
                UPDATE sessions
                SET finished_at = ?, files_imported = ?, files_skipped = ?,
                    status = ?, error_message = ?
                WHERE id = ?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    files_imported,
                    files_skipped,
                    status,
                    error_message,
                    session_id,
                ),
            )

    def recent_imports(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT filename, destination, sha256, capture_time, import_time,
                       camera_name, file_size, session_id
                FROM imports
                ORDER BY import_time DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def recent_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT id, camera_name, camera_serial, started_at, finished_at,
                       files_imported, files_skipped, status, error_message
                FROM sessions
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def stats(self) -> dict[str, Any]:
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) AS count FROM imports").fetchone()
            last = connection.execute(
                "SELECT import_time FROM imports ORDER BY import_time DESC LIMIT 1"
            ).fetchone()
            return {
                "total_imports": total["count"],
                "last_import_at": last["import_time"] if last else None,
            }

    def imported_index(self) -> ImportedIndex:
        """
        Build the pre-download match index from import history.

        Matching on (filename, size) is exact per file, so an interrupted import
        leaves no gap that would be silently skipped later — unlike a single
        high-water sequence number, which assumes everything below it landed.
        """
        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT COALESCE(source_filename, filename) AS filename, file_size
                FROM imports
                """
            ).fetchall()

        signatures: set[tuple[str, int]] = set()
        sizeless: set[str] = set()
        for row in rows:
            filename = row["filename"]
            if not filename:
                continue
            key = filename.casefold()
            size = row["file_size"]
            if size is None:
                sizeless.add(key)
            else:
                signatures.add((key, int(size)))

        return ImportedIndex(
            signatures=frozenset(signatures),
            sizeless_names=frozenset(sizeless),
        )
