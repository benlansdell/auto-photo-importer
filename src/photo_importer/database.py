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

SCHEMA = """
CREATE TABLE IF NOT EXISTS imports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    camera_serial   TEXT,
    camera_name     TEXT,
    filename        TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS watermarks (
    camera_key      TEXT PRIMARY KEY,
    camera_name     TEXT,
    camera_serial   TEXT,
    last_sequence   INTEGER NOT NULL DEFAULT 0,
    last_filename   TEXT,
    updated_at      TEXT NOT NULL
);
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
                        camera_serial, camera_name, filename, capture_time,
                        sha256, destination, file_size, session_id, import_time
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.camera_serial,
                        record.camera_name,
                        record.filename,
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

    def get_watermark(self, camera_key: str) -> int:
        """Return the high-water sequence for a camera, seeding from import history."""
        with self.connection() as connection:
            row = connection.execute(
                "SELECT last_sequence FROM watermarks WHERE camera_key = ?",
                (camera_key,),
            ).fetchone()

        if row is not None and int(row["last_sequence"]) > 0:
            return int(row["last_sequence"])

        seeded = self._seed_watermark_from_imports(camera_key)
        if seeded > 0:
            self.set_watermark(camera_key, seeded, filename=None, camera_name=camera_key)
            return seeded

        return int(row["last_sequence"]) if row is not None else 0

    def known_filenames(self) -> set[str]:
        """Return case-insensitive basenames already recorded in import history."""
        with self.connection() as connection:
            rows = connection.execute("SELECT filename FROM imports").fetchall()
        return {row["filename"].casefold() for row in rows if row["filename"]}

    def set_watermark(
        self,
        camera_key: str,
        sequence: int,
        *,
        filename: str | None = None,
        camera_name: str | None = None,
        camera_serial: str | None = None,
        force: bool = False,
    ) -> None:
        with self.connection() as connection:
            if force:
                connection.execute(
                    """
                    INSERT INTO watermarks (
                        camera_key, camera_name, camera_serial,
                        last_sequence, last_filename, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(camera_key) DO UPDATE SET
                        camera_name = COALESCE(excluded.camera_name, watermarks.camera_name),
                        camera_serial = COALESCE(
                            excluded.camera_serial, watermarks.camera_serial
                        ),
                        last_sequence = excluded.last_sequence,
                        last_filename = excluded.last_filename,
                        updated_at = excluded.updated_at
                    """,
                    (
                        camera_key,
                        camera_name,
                        camera_serial,
                        sequence,
                        filename,
                        datetime.now(UTC).isoformat(),
                    ),
                )
                return

            connection.execute(
                """
                INSERT INTO watermarks (
                    camera_key, camera_name, camera_serial,
                    last_sequence, last_filename, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(camera_key) DO UPDATE SET
                    camera_name = COALESCE(excluded.camera_name, watermarks.camera_name),
                    camera_serial = COALESCE(
                        excluded.camera_serial, watermarks.camera_serial
                    ),
                    last_sequence = MAX(watermarks.last_sequence, excluded.last_sequence),
                    last_filename = CASE
                        WHEN excluded.last_sequence >= watermarks.last_sequence
                        THEN COALESCE(excluded.last_filename, watermarks.last_filename)
                        ELSE watermarks.last_filename
                    END,
                    updated_at = excluded.updated_at
                """,
                (
                    camera_key,
                    camera_name,
                    camera_serial,
                    sequence,
                    filename,
                    datetime.now(UTC).isoformat(),
                ),
            )

    def _seed_watermark_from_imports(self, camera_key: str) -> int:
        """
        Derive an initial watermark from filenames already recorded.

        Prefer rows for this camera key, but fall back to the entire import
        history. Older sessions often stored a null/different camera_name, and
        this appliance is typically one body — a global max is the safe seed.
        """
        from photo_importer.watermark import filename_sequence

        with self.connection() as connection:
            rows = connection.execute(
                """
                SELECT filename FROM imports
                WHERE camera_serial = ? OR camera_name = ?
                """,
                (camera_key, camera_key),
            ).fetchall()
            if not rows:
                rows = connection.execute("SELECT filename FROM imports").fetchall()

        sequences = [
            seq
            for row in rows
            if (seq := filename_sequence(row["filename"])) is not None
        ]
        return max(sequences) if sequences else 0
