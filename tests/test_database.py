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


def test_watermark_advances_and_can_be_forced_down(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.set_watermark("Fuji X-T3", 100, filename="DSCF0100.RAF")
    database.set_watermark("Fuji X-T3", 50, filename="DSCF0050.RAF")
    assert database.get_watermark("Fuji X-T3") == 100

    database.set_watermark("Fuji X-T3", 0, force=True)
    assert database.get_watermark("Fuji X-T3") == 0


def test_watermark_seeds_from_import_history(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(
        ImportRecord(
            camera_serial=None,
            camera_name="Fuji Fujifilm X-T3",
            filename="DSCF0420.RAF",
            capture_time=None,
            sha256="b" * 64,
            destination="/photos/DSCF0420.RAF",
            file_size=1,
            session_id="s",
            import_time="2026-08-01T00:00:00+00:00",
        )
    )
    assert database.get_watermark("Fuji Fujifilm X-T3") == 420


def test_watermark_seeds_from_all_imports_when_camera_name_mismatches(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(
        ImportRecord(
            camera_serial=None,
            camera_name=None,
            filename="DSCF0999.JPG",
            capture_time=None,
            sha256="c" * 64,
            destination="/photos/DSCF0999.JPG",
            file_size=1,
            session_id="s",
            import_time="2026-08-01T00:00:00+00:00",
        )
    )
    # Detected model string doesn't match stored camera_name — still seed.
    assert database.get_watermark("Fuji Fujifilm X-T3") == 999


def test_known_filenames(tmp_path: Path):
    database = Database(tmp_path / "imports.sqlite3")
    database.record_import(
        ImportRecord(
            camera_serial=None,
            camera_name=None,
            filename="DSCF0001.RAF",
            capture_time=None,
            sha256="d" * 64,
            destination="/photos/DSCF0001.RAF",
            file_size=1,
            session_id="s",
            import_time="2026-08-01T00:00:00+00:00",
        )
    )
    assert database.known_filenames() == {"dscf0001.raf"}
