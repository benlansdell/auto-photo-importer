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


def _record(filename: str, digest: str, *, size: int = 100, source: str | None = None):
    return ImportRecord(
        camera_serial=None,
        camera_name="Fujifilm X-T3",
        filename=filename,
        capture_time=None,
        sha256=digest,
        destination=f"/photos/{filename}",
        file_size=size,
        session_id="s",
        import_time="2026-08-01T00:00:00+00:00",
        source_filename=source,
    )


def test_imported_index_matches_name_and_size(tmp_path: Path):
    """Stored size is bytes; the value probed against it is the listing's KB."""
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(_record("DSCF0001.RAF", "b" * 64, size=2048 * 1024))
    index = database.imported_index()

    assert index.contains("DSCF0001.RAF", 2048) is True
    assert index.contains("dscf0001.raf", 2048) is True
    # Same name after a counter rollover, different photo, different size.
    assert index.contains("DSCF0001.RAF", 9999) is False
    assert index.contains("DSCF0002.RAF", 2048) is False


def test_imported_index_uses_camera_filename_when_library_name_differs(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(
        _record("DSCF0001-1.RAF", "c" * 64, size=512 * 1024, source="DSCF0001.RAF")
    )
    index = database.imported_index()

    assert index.contains("DSCF0001.RAF", 512) is True


def test_imported_index_falls_back_to_name_for_legacy_rows(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(_record("DSCF0007.JPG", "d" * 64, size=None))
    index = database.imported_index()

    assert index.contains("DSCF0007.JPG", 1234) is True


def test_imported_index_handles_same_name_at_two_sizes(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(_record("DSCF0001.JPG", "f" * 64, size=100 * 1024))
    database.record_import(
        _record("DSCF0001_1.JPG", "0" * 64, size=300 * 1024, source="DSCF0001.JPG")
    )
    index = database.imported_index()

    assert index.contains("DSCF0001.JPG", 100) is True
    assert index.contains("DSCF0001.JPG", 300) is True
    assert index.contains("DSCF0001.JPG", 200) is False


def test_gap_below_highest_sequence_is_still_downloadable(tmp_path: Path):
    """The old high-water mark hid gaps; per-file matching must not."""
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(_record("DSCF2901.RAF", "e" * 64, size=10 * 1024))
    index = database.imported_index()

    assert index.contains("DSCF2901.RAF", 10) is True
    assert index.contains("DSCF1500.RAF", 10) is False
