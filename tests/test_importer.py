"""Import session behaviour around camera re-enumeration."""

from pathlib import Path

import pytest

from photo_importer import camera as cam
from photo_importer.config import AppConfig
from photo_importer.database import Database
from photo_importer.importer import ImportSession


@pytest.fixture
def session(tmp_path: Path) -> ImportSession:
    config = AppConfig(
        destination=tmp_path / "photos",
        staging_dir=tmp_path / "staging",
        database_path=tmp_path / "imports.sqlite3",
        log_dir=tmp_path / "logs",
        camera_settle_seconds=0,
        retry_delay_seconds=0,
    )
    config.ensure_dirs()
    return ImportSession(config, Database(config.database_path))


def test_current_port_follows_reenumeration(session: ImportSession, monkeypatch):
    stale = cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,003")
    monkeypatch.setattr(
        cam,
        "camera_present",
        lambda _filter: cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,007"),
    )

    assert session._current_port(stale) == "usb:003,007"


def test_current_port_falls_back_to_autodetect(session: ImportSession, monkeypatch):
    stale = cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,003")
    monkeypatch.setattr(cam, "camera_present", lambda _filter: None)

    assert session._current_port(stale) is None


def test_download_retries_with_refreshed_port(session: ImportSession, monkeypatch):
    detected = cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,003")
    ports = ["usb:003,004", "usb:003,009"]
    monkeypatch.setattr(
        cam,
        "camera_present",
        lambda _filter: cam.DetectedCamera(model=detected.model, port=ports.pop(0)),
    )

    attempted: list[str | None] = []

    def fake_download(_staging: Path, *, port: str | None, skip_existing: bool) -> None:
        attempted.append(port)
        if len(attempted) == 1:
            raise cam.CameraError("Error (-52: 'Could not find the requested device')")

    monkeypatch.setattr(cam, "download_all", fake_download)

    session._download_with_retries(detected, session.config.staging_dir)

    assert attempted == ["usb:003,004", "usb:003,009"]
