"""Database and duplicate-detection tests."""

from pathlib import Path

from photo_importer.database import Database, ImportRecord


def test_hash_deduplication(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    record = ImportRecord(
        camera_serial="ABC",
        camera_name="Fujifilm X-T3",
        filename="DSCF0001.RAF",
        capture_time="2026-07-31T12:00:00",
        sha256="a" * 64,
        destination="/photos/2026/07/31/DSCF0001.RAF",
        file_size=100,
        session_id="session-1",
        import_time="2026-07-31T13:00:00+00:00",
    )

    assert database.record_import(record) is True
    assert database.has_hash("a" * 64) is True
    assert database.record_import(record) is False
    assert database.stats()["total_imports"] == 1


def test_session_history(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.start_session("session-1", "Fujifilm X-T3", "ABC")
    database.finish_session("session-1", files_imported=3, files_skipped=1)

    sessions = database.recent_sessions()
    assert len(sessions) == 1
    assert sessions[0]["files_imported"] == 3
    assert sessions[0]["status"] == "success"
