"""Decide which camera files still need downloading."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImportedIndex:
    """
    Everything already in the import history, keyed for pre-download matching.

    ``signatures`` holds (lowercased filename, byte size) pairs, which is precise
    enough to survive a camera filename-counter rollover: a reused name with
    different content has a different size. ``sizeless_names`` covers legacy rows
    that predate size tracking, where the name alone has to be trusted.
    """

    signatures: frozenset[tuple[str, int]]
    sizeless_names: frozenset[str]

    def contains(self, name: str, size: int | None) -> bool:
        key = name.casefold()
        if size is not None and (key, size) in self.signatures:
            return True
        return key in self.sizeless_names


def partition_new_files(files: list, index: ImportedIndex) -> tuple[list, list]:
    """
    Split camera files into (to_download, already_imported).

    A file whose size cannot be read from the listing is downloaded so the
    SHA-256 check can make the final call.
    """
    to_download: list = []
    already: list = []
    for item in files:
        name = getattr(item, "name", str(item))
        size = getattr(item, "size", None)
        if index.contains(name, size):
            already.append(item)
        else:
            to_download.append(item)
    return to_download, already
