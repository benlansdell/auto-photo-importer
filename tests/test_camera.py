"""Camera detection parsing tests (no real hardware)."""

from photo_importer.camera import DetectedCamera, detect_cameras


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
