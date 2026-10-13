"""High-water mark helpers."""

from photo_importer.camera import CameraFile
from photo_importer.watermark import (
    filename_sequence,
    files_after_watermark,
    max_sequence,
    should_reset_watermark,
)


def test_filename_sequence_fujifilm():
    assert filename_sequence("DSCF1234.RAF") == 1234
    assert filename_sequence("DSCF1234.JPG") == 1234
    assert filename_sequence("IMG_0007.JPG") == 7
    assert filename_sequence("notes.txt") is None


def test_files_after_watermark_skips_at_or_below():
    files = [
        CameraFile(1, "DSCF0100.RAF", "/DCIM/100_FUJI"),
        CameraFile(2, "DSCF0100.JPG", "/DCIM/100_FUJI"),
        CameraFile(3, "DSCF0101.RAF", "/DCIM/100_FUJI"),
        CameraFile(4, "DSCF0101.JPG", "/DCIM/100_FUJI"),
    ]
    keep, skipped = files_after_watermark(files, 100)
    assert [item.name for item in keep] == ["DSCF0101.RAF", "DSCF0101.JPG"]
    assert skipped == 2


def test_files_after_watermark_zero_downloads_all():
    files = [CameraFile(1, "DSCF0100.RAF", "/DCIM/100_FUJI")]
    keep, skipped = files_after_watermark(files, 0)
    assert keep == files
    assert skipped == 0


def test_should_reset_when_camera_counter_rolls_over():
    assert should_reset_watermark([1, 2, 3], watermark=1500) is True
    assert should_reset_watermark([1490, 1500, 1510], watermark=1500) is False
    assert should_reset_watermark([], watermark=1500) is False


def test_max_sequence():
    assert max_sequence(["DSCF0009.RAF", "DSCF0010.JPG"]) == 10
    assert max_sequence(["readme.txt"]) is None
