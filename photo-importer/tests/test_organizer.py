"""Verifier and organizer unit tests."""

from datetime import datetime
from pathlib import Path

from photo_importer.config import AppConfig
from photo_importer.organizer import destination_dir, organize_files, pair_key
from photo_importer.verifier import hash_directory, sha256_file


def test_sha256_file(tmp_path: Path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"hello")
    assert sha256_file(p) == sha256_file(p)
    q = tmp_path / "b.bin"
    q.write_bytes(b"hello!")
    assert sha256_file(p) != sha256_file(q)


def test_hash_directory(tmp_path: Path):
    (tmp_path / "x.RAF").write_bytes(b"raw")
    (tmp_path / "x.JPG").write_bytes(b"jpg")
    (tmp_path / "notes.txt").write_text("skip")
    hashes = hash_directory(tmp_path, extensions={"RAF", "JPG"})
    assert len(hashes) == 2


def test_destination_dir():
    root = Path("/photos")
    dt = datetime(2026, 7, 31, 12, 0, 0)
    assert destination_dir(root, dt, "ymd") == Path("/photos/2026/07/31")
    assert destination_dir(root, dt, "ym_ymd") == Path("/photos/2026/2026-07-31")


def test_pair_key():
    assert pair_key(Path("DSCF0001.RAF")) == pair_key(Path("DSCF0001.JPG"))


def test_organize_keeps_pairs(tmp_path: Path, monkeypatch):
    staging = tmp_path / "staging"
    dest = tmp_path / "photos"
    staging.mkdir()
    dest.mkdir()
    (staging / "DSCF0001.RAF").write_bytes(b"rawdata")
    (staging / "DSCF0001.JPG").write_bytes(b"jpgdata")

    # Avoid depending on exiftool in CI
    monkeypatch.setattr(
        "photo_importer.organizer.get_capture_time",
        lambda _p: datetime(2026, 7, 31, 10, 0, 0),
    )

    cfg = AppConfig(destination=dest, staging_dir=staging, preserve_filenames=True)
    organized = organize_files(staging, cfg, hashes={})
    assert len(organized) == 2
    dests = {o.destination for o in organized}
    assert all(d.parent == dest / "2026" / "07" / "31" for d in dests)
    assert (dest / "2026" / "07" / "31" / "DSCF0001.RAF").is_file()
    assert (dest / "2026" / "07" / "31" / "DSCF0001.JPG").is_file()
