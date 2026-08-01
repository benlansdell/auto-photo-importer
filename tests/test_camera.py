"""Camera detection parsing tests (no real hardware)."""

import pytest

from photo_importer.camera import (
    CameraError,
    DetectedCamera,
    clean_message,
    detect_cameras,
    list_files,
)


def test_detect_cameras_parses(monkeypatch):
    fake = """Model                          Port
----------------------------------------------------------
Fujifilm X-T3                  usb:001,004
Mass Storage Camera            usb:001,005
"""

    class Result:
        stdout = fake
        stderr = ""
        returncode = 0

    monkeypatch.setattr("photo_importer.camera._run", lambda *a, **k: Result())
    cams = detect_cameras("Fujifilm")
    assert cams == [DetectedCamera(model="Fujifilm X-T3", port="usb:001,004")]


def test_detect_no_match(monkeypatch):
    class Result:
        stdout = (
            "Model                          Port\n"
            "----\n"
            "Canon EOS R5                  usb:001,002\n"
        )
        stderr = ""
        returncode = 0

    monkeypatch.setattr("photo_importer.camera._run", lambda *a, **k: Result())
    assert detect_cameras("Fujifilm") == []


def test_clean_message_condenses_gphoto2_banner():
    raw = (
        "*** Error ***              \n"
        "An error occurred in the io-library ('Could not find the requested "
        "device on the USB port'): No error description available\n"
        "*** Error (-52: 'Could not find the requested device on the USB port') ***\n"
    )

    cleaned = clean_message(raw)

    assert "\n" not in cleaned
    assert "No error description available" not in cleaned
    assert "-52" in cleaned


def test_list_files_raises_on_failure(monkeypatch):
    class Result:
        stdout = ""
        stderr = "*** Error (-52: 'Could not find the requested device') ***"
        returncode = 1

    monkeypatch.setattr("photo_importer.camera._run", lambda *a, **k: Result())
    with pytest.raises(CameraError, match="-52"):
        list_files("usb:003,003")
