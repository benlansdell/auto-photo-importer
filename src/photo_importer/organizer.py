"""Organize downloaded files into date-based library folders."""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from photo_importer.config import AppConfig
from photo_importer.logger import get_logger
from photo_importer.verifier import sha256_file

log = get_logger("organizer")


@dataclass
class OrganizedFile:
    source: Path
    destination: Path
    capture_time: datetime | None
    sha256: str | None = None
    # True when an identical file was already in the library, so nothing moved.
    already_present: bool = False


def _run_exiftool(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["exiftool", *args],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except FileNotFoundError:
        log.warning("exiftool not found; falling back to filesystem mtime")
        return ""
    return result.stdout or ""


def get_capture_time(path: Path) -> datetime | None:
    """Read DateTimeOriginal (or fallbacks) from EXIF."""
    output = _run_exiftool(
        [
            "-s3",
            "-DateTimeOriginal",
            "-CreateDate",
            "-MediaCreateDate",
            "-FileModifyDate",
            str(path),
        ]
    )
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        # exiftool -s3 prints bare values when one tag; with multiple it may still work
        parsed = _parse_exif_datetime(line)
        if parsed:
            return parsed

    # Fallback: filesystem mtime
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return None


def _parse_exif_datetime(value: str) -> datetime | None:
    value = value.strip()
    # Common: 2026:07:31 19:52:01
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            # Strip trailing timezone junk like +00:00 if fmt has no %z and value has it
            cleaned = re.sub(r"([+-]\d{2}):(\d{2})$", r"\1\2", value)
            return datetime.strptime(cleaned[:19] if "%z" not in fmt else cleaned, fmt)
        except ValueError:
            continue
    return None


def destination_dir(root: Path, capture: datetime | None, layout: str) -> Path:
    dt = capture or datetime.now(UTC)
    if layout == "ym_ymd":
        return root / f"{dt:%Y}" / f"{dt:%Y-%m-%d}"
    # default ymd
    return root / f"{dt:%Y}" / f"{dt:%m}" / f"{dt:%d}"


def pair_key(path: Path) -> str:
    """Stem used to keep RAW+JPEG pairs together."""
    return path.stem.upper()


def organize_files(
    staging_dir: Path,
    config: AppConfig,
    *,
    hashes: dict[Path, str] | None = None,
) -> list[OrganizedFile]:
    """
    Move files from staging into the destination library.

    RAW+JPEG pairs that share a stem land in the same date folder (using the
    earliest capture time found for the pair).
    """
    files = [p for p in staging_dir.rglob("*") if p.is_file()]
    if not files:
        return []

    # Group by stem so pairs stay together
    groups: dict[str, list[Path]] = {}
    for path in files:
        groups.setdefault(pair_key(path), []).append(path)

    organized: list[OrganizedFile] = []

    for _stem, members in sorted(groups.items()):
        times = [t for t in (get_capture_time(p) for p in members) if t is not None]
        capture = min(times) if times else None
        dest_folder = destination_dir(config.destination, capture, config.folder_layout)
        dest_folder.mkdir(parents=True, exist_ok=True)

        for src in members:
            dest_name = src.name
            if not config.preserve_filenames and capture is not None:
                dest_name = f"{capture:%Y%m%d_%H%M%S}_{src.name}"

            dest = dest_folder / dest_name
            digest = (hashes or {}).get(src)

            existing = find_identical(dest, src, digest)
            if existing is not None:
                log.info(
                    "Already in library, not copying %s (identical to %s)",
                    src.name,
                    existing,
                )
                if not config.dry_run:
                    src.unlink(missing_ok=True)
                organized.append(
                    OrganizedFile(
                        source=src,
                        destination=existing,
                        capture_time=capture,
                        sha256=digest,
                        already_present=True,
                    )
                )
                continue

            dest = _unique_path(dest)

            if config.dry_run:
                log.info("[dry-run] would move %s -> %s", src, dest)
            else:
                shutil.move(str(src), str(dest))
                log.info("Moved %s -> %s", src.name, dest)

            organized.append(
                OrganizedFile(
                    source=src,
                    destination=dest,
                    capture_time=capture,
                    sha256=digest,
                )
            )

    return organized


def _candidate_paths(dest: Path, limit: int = 50) -> Iterator[Path]:
    """``dest`` plus any ``_1``, ``_2`` … siblings a previous run may have made."""
    yield dest
    for counter in range(1, limit + 1):
        candidate = dest.parent / f"{dest.stem}_{counter}{dest.suffix}"
        if not candidate.exists():
            return
        yield candidate


def find_identical(dest: Path, src: Path, digest: str | None = None) -> Path | None:
    """
    Return an existing library file with the same content as ``src``, if any.

    Guards against creating ``DSCF0001_1.RAF`` copies of photos the library
    already holds — which is what happens on a name collision when the import
    database has lost the corresponding hash.
    """
    try:
        src_size = src.stat().st_size
    except OSError:
        return None

    src_digest = digest
    for candidate in _candidate_paths(dest):
        if not candidate.is_file():
            continue
        try:
            if candidate.stat().st_size != src_size:
                continue
        except OSError:
            continue
        if src_digest is None:
            src_digest = sha256_file(src)
        if sha256_file(candidate) == src_digest:
            return candidate
    return None


def _unique_path(path: Path) -> Path:
    """Avoid overwriting a *different* photo: DSCF0001.RAF -> DSCF0001_1.RAF."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1
