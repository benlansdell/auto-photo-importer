"""SHA-256 verification helpers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from photo_importer.logger import get_logger

log = get_logger("verifier")

CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_copy(source: Path, destination: Path) -> bool:
    """Return True if both files exist and share the same SHA-256."""
    if not source.is_file() or not destination.is_file():
        return False
    if source.stat().st_size != destination.stat().st_size:
        log.warning(
            "Size mismatch %s (%s) vs %s (%s)",
            source,
            source.stat().st_size,
            destination,
            destination.stat().st_size,
        )
        return False
    src_hash = sha256_file(source)
    dst_hash = sha256_file(destination)
    if src_hash != dst_hash:
        log.error("Hash mismatch for %s -> %s", source.name, destination)
        return False
    return True


def hash_directory(directory: Path, extensions: set[str] | None = None) -> dict[Path, str]:
    """Compute SHA-256 for every file under directory (optionally filtered)."""
    results: dict[Path, str] = {}
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if extensions is not None and path.suffix.lstrip(".").upper() not in extensions:
            continue
        results[path] = sha256_file(path)
        log.debug("Hashed %s -> %s", path.name, results[path][:12])
    return results
