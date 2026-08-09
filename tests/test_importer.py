"""Import session behaviour around camera re-enumeration and selective download."""

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


def _sample_files() -> list[cam.CameraFile]:
    return [
        cam.CameraFile(1, "DSCF0001.RAF", "/store/DCIM/100_FUJI"),
        cam.CameraFile(2, "DSCF0001.JPG", "/store/DCIM/100_FUJI"),
    ]


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


def test_download_error_explains_missing_usb_node(session: ImportSession, monkeypatch):
    detected = cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,003")
    monkeypatch.setattr(cam, "camera_present", lambda _filter: detected)
    monkeypatch.setattr(cam, "missing_usb_node", lambda _port: "/dev/bus/usb/003/003")

    def fail(_staging, files, *, port, on_file=None):
        raise cam.CameraError("Error (-52: 'Could not find the requested device')")

    monkeypatch.setattr(cam, "download_files", fail)

    with pytest.raises(cam.CameraError, match="devices:"):
        session._download_with_retries(detected, session.config.staging_dir, files=_sample_files())


def test_download_retries_with_refreshed_port(session: ImportSession, monkeypatch):
    detected = cam.DetectedCamera(model="Fujifilm X-T3", port="usb:003,003")
    ports = ["usb:003,004", "usb:003,009"]
    monkeypatch.setattr(
        cam,
        "camera_present",
        lambda _filter: cam.DetectedCamera(model=detected.model, port=ports.pop(0)),
    )

    attempted: list[str | None] = []

    def fake_download(_staging, files, *, port: str | None, on_file=None):
        attempted.append(port)
        if len(attempted) == 1:
            raise cam.CameraError("Error (-52: 'Could not find the requested device')")
        if on_file:
            on_file("DSCF0001.RAF")

    monkeypatch.setattr(cam, "download_files", fake_download)

    session._download_with_retries(detected, session.config.staging_dir, files=_sample_files())

    assert attempted == ["usb:003,004", "usb:003,009"]
