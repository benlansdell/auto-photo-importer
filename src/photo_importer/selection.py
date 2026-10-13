"""Decide which camera files still need downloading."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


def _kb_variants(size_bytes: int) -> tuple[int, int]:
    """
    KB values gphoto2 could print for a file of ``size_bytes``.

    Different gphoto2 builds use floor or ceiling division, so both are treated
    as a match rather than applying a fuzzy tolerance.
    """
    floor_kb = size_bytes // 1024
    ceil_kb = -(-size_bytes // 1024)
    return floor_kb, ceil_kb


@dataclass(frozen=True)
class ImportedIndex:
    """
    Import history keyed for matching against a camera listing.

    ``byte_sizes`` maps a lowercased camera filename to the library byte sizes
    recorded for it. Camera listings report KB, so sizes are compared through
    :func:`_kb_variants` instead of directly.

    ``sizeless_names`` covers legacy rows imported before sizes were stored,
    where the filename alone has to be trusted.
    """

    byte_sizes: Mapping[str, frozenset[int]]
    sizeless_names: frozenset[str]

    def has_name(self, name: str) -> bool:
        key = name.casefold()
        return key in self.sizeless_names or key in self.byte_sizes

    def contains(self, name: str, size_kb: int | None) -> bool:
        key = name.casefold()
        if key in self.sizeless_names:
            return True
        known = self.byte_sizes.get(key)
        if not known or size_kb is None:
            return False
        return any(size_kb in _kb_variants(size) for size in known)


def partition_new_files(
    files: list, index: ImportedIndex, *, match_names_only: bool = False
) -> tuple[list, list]:
    """
    Split camera files into (to_download, already_imported).

    With ``match_names_only`` the size is ignored, for cameras whose listing does
    not report one. That is weaker — a filename reused after a counter rollover
    looks like a file already held — but downloading the entire card every time
    is worse, and SHA-256 still decides what actually gets imported.
    """
    to_download: list = []
    already: list = []
    for item in files:
        name = getattr(item, "name", str(item))
        size_kb = getattr(item, "size_kb", None)
        known = index.has_name(name) if match_names_only else index.contains(name, size_kb)
        if known:
            already.append(item)
        else:
            to_download.append(item)
    return to_download, already


def sizes_reported(files: list) -> int:
    """How many listed files came with a size (used to pick the matching mode)."""
    return sum(1 for item in files if getattr(item, "size_kb", None) is not None)
