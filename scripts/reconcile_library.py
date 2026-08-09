#!/usr/bin/env python3
"""
Reconcile an existing photo library with the import database.

Repairs the state left behind when the library and the database disagree — the
symptom being ``DSCF0001_1.JPG`` copies of photos already present, created
because a name collision could not be recognised as identical content.

Two passes:

1. **De-duplicate.** Files sharing a SHA-256 are collapsed to one copy. The
   copy with a collision suffix (``_1``, ``-2``) is the one removed, and only
   when the un-suffixed sibling it was derived from is the file being kept.
2. **Index.** Every surviving file is recorded in the database, keyed by the
   filename it would have on the camera, so the next import recognises it and
   skips the download.

Nothing is deleted or written without ``--apply``.

    python /app/scripts/reconcile_library.py                 # report only
    python /app/scripts/reconcile_library.py --apply         # make changes
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from photo_importer.config import default_config_path, load_config
from photo_importer.database import Database, ImportRecord
from photo_importer.verifier import sha256_file

# Trailing "_1" / "-12" added by the organizer when a destination name was taken.
SUFFIX_RE = re.compile(r"^(?P<base>.+?)[-_](?P<counter>\d+)$")


def unsuffixed_sibling(path: Path) -> Path | None:
    """
    Return the path this one looks like a collision copy of, if it exists.

    Requiring the sibling to exist keeps camera-native names such as
    ``IMG_1234.JPG`` from being mistaken for suffixed copies.
    """
    match = SUFFIX_RE.match(path.stem)
    if match is None:
        return None
    candidate = path.with_name(f"{match.group('base')}{path.suffix}")
    if candidate == path or not candidate.is_file():
        return None
    return candidate


def camera_filename(path: Path) -> str:
    """The name this file would have had on the camera."""
    sibling = unsuffixed_sibling(path)
    return sibling.name if sibling is not None else path.name


def capture_time_from_path(path: Path, root: Path) -> str | None:
    """Derive a capture date from the YYYY/MM/DD library layout, if it matches."""
    try:
        parts = path.relative_to(root).parts[:-1]
    except ValueError:
        return None
    if len(parts) >= 3 and all(part.isdigit() for part in parts[:3]):
        year, month, day = parts[0], parts[1], parts[2]
        try:
            return datetime(int(year), int(month), int(day)).isoformat()
        except ValueError:
            return None
    if len(parts) >= 2 and "-" in parts[1]:
        try:
            return datetime.strptime(parts[1], "%Y-%m-%d").isoformat()
        except ValueError:
            return None
    return None


def collect_files(root: Path, extensions: set[str]) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lstrip(".").upper() in extensions
    )


def rank_for_keeping(path: Path) -> tuple[int, int, str]:
    """Sort key that prefers un-suffixed, then shorter, then stable names."""
    return (1 if unsuffixed_sibling(path) is not None else 0, len(path.name), str(path))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument(
        "--library",
        type=Path,
        default=None,
        help="Library root to scan (default: destination from config)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete duplicates and write database rows",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Index files but never delete duplicates",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config or default_config_path())
    root = (args.library or config.destination).resolve()
    if not root.is_dir():
        print(f"Library root not found: {root}", file=sys.stderr)
        return 1

    extensions = {ext.upper() for ext in (*config.raw_extensions, *config.jpeg_extensions)}
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"[{mode}] library={root} db={config.database_path}")
    print(f"[{mode}] extensions={','.join(sorted(extensions))}")

    files = collect_files(root, extensions)
    if not files:
        print("No media files found — nothing to do.")
        return 0
    print(f"Hashing {len(files)} files…")

    by_hash: dict[str, list[Path]] = defaultdict(list)
    for index, path in enumerate(files, start=1):
        by_hash[sha256_file(path)].append(path)
        if index % 250 == 0:
            print(f"  hashed {index}/{len(files)}")

    keepers: list[tuple[Path, str]] = []
    removable: list[tuple[Path, Path]] = []
    for digest, paths in by_hash.items():
        ordered = sorted(paths, key=rank_for_keeping)
        keeper = ordered[0]
        keepers.append((keeper, digest))
        removable.extend((duplicate, keeper) for duplicate in ordered[1:])

    print(
        f"\n{len(by_hash)} unique photos, "
        f"{len(removable)} redundant cop{'y' if len(removable) == 1 else 'ies'}"
    )

    deleted = 0
    if removable and not args.no_delete:
        for duplicate, keeper in removable:
            note = "" if duplicate.parent == keeper.parent else "  (different folder)"
            print(f"  duplicate: {duplicate}  ==  {keeper}{note}")
            if args.apply:
                duplicate.unlink(missing_ok=True)
                deleted += 1
    elif removable:
        print("  (--no-delete: leaving duplicates in place)")

    database = Database(config.database_path)
    indexed = 0
    already = 0
    for path, digest in keepers:
        if not path.is_file():
            continue
        record = ImportRecord(
            camera_serial=None,
            camera_name=config.camera_name,
            filename=path.name,
            capture_time=capture_time_from_path(path, root),
            sha256=digest,
            destination=str(path),
            file_size=path.stat().st_size,
            session_id="reconcile",
            import_time=datetime.now(UTC).isoformat(),
            source_filename=camera_filename(path),
        )
        if not args.apply:
            if not database.has_hash(digest):
                indexed += 1
            else:
                already += 1
            continue
        if database.record_import(record):
            indexed += 1
        else:
            already += 1

    verb = "Indexed" if args.apply else "Would index"
    print(f"\n{verb} {indexed} files ({already} already known)")
    if args.apply:
        print(f"Deleted {deleted} duplicate files")
    else:
        print("\nNothing was changed. Re-run with --apply to delete duplicates and index.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
