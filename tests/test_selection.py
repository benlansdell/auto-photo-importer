"""Pre-download file selection tests."""

from photo_importer.camera import CameraFile
from photo_importer.selection import ImportedIndex, partition_new_files


def _index(signatures=(), sizeless=()):
    return ImportedIndex(
        signatures=frozenset(signatures),
        sizeless_names=frozenset(sizeless),
    )


def test_known_files_are_skipped_and_new_ones_kept():
    files = [
        CameraFile(1, "DSCF0001.RAF", "/store/DCIM", 100),
        CameraFile(2, "DSCF0002.RAF", "/store/DCIM", 200),
    ]
    index = _index(signatures={("dscf0001.raf", 100)})

    keep, already = partition_new_files(files, index)

    assert [item.name for item in keep] == ["DSCF0002.RAF"]
    assert [item.name for item in already] == ["DSCF0001.RAF"]


def test_same_name_different_size_is_treated_as_new():
    """A camera counter rollover reuses names; size keeps them distinguishable."""
    files = [CameraFile(1, "DSCF0001.RAF", "/store/DCIM", 999)]
    index = _index(signatures={("dscf0001.raf", 100)})

    keep, already = partition_new_files(files, index)

    assert len(keep) == 1
    assert already == []


def test_unknown_size_defers_to_hashing():
    files = [CameraFile(1, "DSCF0001.RAF", "/store/DCIM", None)]
    index = _index(signatures={("dscf0001.raf", 100)})

    keep, _ = partition_new_files(files, index)

    assert len(keep) == 1


def test_legacy_name_only_history_still_skips():
    files = [CameraFile(1, "DSCF0001.RAF", "/store/DCIM", 100)]
    index = _index(sizeless={"dscf0001.raf"})

    keep, already = partition_new_files(files, index)

    assert keep == []
    assert len(already) == 1


def test_gap_in_history_is_downloaded():
    files = [
        CameraFile(1, "DSCF1500.RAF", "/store/DCIM", 100),
        CameraFile(2, "DSCF2901.RAF", "/store/DCIM", 100),
    ]
    index = _index(signatures={("dscf2901.raf", 100)})

    keep, _ = partition_new_files(files, index)

    assert [item.name for item in keep] == ["DSCF1500.RAF"]
