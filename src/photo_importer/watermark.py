"""Filename sequence helpers for the import high-water mark."""

from __future__ import annotations

import re
from pathlib import Path


def filename_sequence(name: str) -> int | None:
    """Extract the trailing numeric sequence from a camera filename, if any."""
    stem = Path(name).stem
    match = re.search(r"(\d+)$", stem)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def max_sequence(names: list[str]) -> int | None:
    sequences = [seq for name in names if (seq := filename_sequence(name)) is not None]
    return max(sequences) if sequences else None


def files_after_watermark(files: list, watermark: int) -> tuple[list, int]:
    """
    Split camera files into (to_download, skipped_count).

    Files without a parseable sequence are always downloaded. A watermark of 0
    downloads everything.
    """
    if watermark <= 0:
        return list(files), 0

    keep: list = []
    skipped = 0
    for item in files:
        name = getattr(item, "name", str(item))
        seq = filename_sequence(name)
        if seq is None or seq > watermark:
            keep.append(item)
        else:
            skipped += 1
    return keep, skipped


def should_reset_watermark(camera_sequences: list[int], watermark: int) -> bool:
    """
    Detect a filename-counter reset.

    If the camera's highest sequence is strictly less than the watermark, the
    body almost certainly started numbering over (fresh card / reset).
    """
    if watermark <= 0 or not camera_sequences:
        return False
    return max(camera_sequences) < watermark
