"""Camera detection parsing tests (no real hardware)."""

from pathlib import Path

import pytest

from photo_importer.camera import (
    CameraError,
    CameraFile,
    DetectedCamera,
    _format_index_ranges,
    _stream,
    clean_message,
    detect_cameras,
    list_files,
    missing_usb_node,
    parse_file_list,
    usb_node_path,
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
            "Model                          Port\n----\nCanon EOS R5                  usb:001,002\n"
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


def test_stream_reports_each_file_once(tmp_path: Path):
    script = tmp_path / "fake-gphoto2.sh"
    script.write_text(
        "#!/bin/sh\n"
        "echo \"Downloading 'DSCF0001.RAF' from folder '/store/DCIM'...\"\n"
        'echo "Saving file as /staging/DSCF0001.RAF"\n'
        "echo \"Downloading 'DSCF0002.JPG' from folder '/store/DCIM'...\"\n"
        'echo "Saving file as /staging/DSCF0002.JPG"\n'
    )
    script.chmod(0o755)

    seen: list[str] = []
    returncode, output = _stream([str(script)], on_file=seen.append)

    assert returncode == 0
    assert seen == ["DSCF0001.RAF", "DSCF0002.JPG"]
    assert "Saving file as" in output


def test_stream_returns_exit_code_and_output(tmp_path: Path):
    script = tmp_path / "failing.sh"
    script.write_text("#!/bin/sh\necho 'boom' >&2\nexit 1\n")
    script.chmod(0o755)

    returncode, output = _stream([str(script)])

    assert returncode == 1
    assert "boom" in output


def test_usb_node_path():
    assert usb_node_path("usb:003,004") == Path("/dev/bus/usb/003/004")
    assert usb_node_path("ptpip:192.168.1.5") is None


def test_missing_usb_node_reports_absent_node(monkeypatch, tmp_path: Path):
    usb_root = tmp_path / "dev" / "bus" / "usb"
    (usb_root / "003").mkdir(parents=True)
    monkeypatch.setattr(
        "photo_importer.camera.usb_node_path",
        lambda port: usb_root / "003" / port.split(",")[-1],
    )

    assert missing_usb_node("usb:003,004") == usb_root / "003" / "004"

    (usb_root / "003" / "004").write_bytes(b"")
    assert missing_usb_node("usb:003,004") is None


def test_missing_usb_node_ignores_hosts_without_usb_tree():
    # No /dev/bus/usb (e.g. macOS dev machine) must not produce false positives.
    assert missing_usb_node(None) is None


def test_parse_file_list_tracks_folders_and_indices():
    output = """
There are 2 files in folder '/store_00010001/DCIM/100_FUJI'.
#1     DSCF0001.RAF               rd  25123456 image/x-fuji-raf
#2     DSCF0001.JPG               rd   5123456 image/jpeg
There is 1 file in folder '/store_00010001/DCIM/101_FUJI'.
#1     DSCF0002.RAF               rd  25123456 image/x-fuji-raf
"""
    files = parse_file_list(output)
    assert files == [
        CameraFile(1, "DSCF0001.RAF", "/store_00010001/DCIM/100_FUJI", 25123456),
        CameraFile(2, "DSCF0001.JPG", "/store_00010001/DCIM/100_FUJI", 5123456),
        CameraFile(1, "DSCF0002.RAF", "/store_00010001/DCIM/101_FUJI", 25123456),
    ]


def test_parse_file_list_tolerates_missing_size():
    output = """
There is 1 file in folder '/store_00010001/DCIM/100_FUJI'.
#1     DSCF0009.RAF
"""
    assert parse_file_list(output) == [
        CameraFile(1, "DSCF0009.RAF", "/store_00010001/DCIM/100_FUJI", None)
    ]


def test_format_index_ranges():
    assert _format_index_ranges([1, 2, 3, 5, 7, 8]) == "1-3,5,7-8"
    assert _format_index_ranges([4]) == "4"
