#!/usr/bin/env python3
"""
Explain why the importer considers camera files new or already imported.

Read-only: detects the camera, lists its files, and compares that listing to the
import database without downloading, moving, or writing anything.

Use it when a plug reports "downloading N new" for photos already in the library.
It shows the raw gphoto2 lines, whether sizes were parsed from them, and the
per-file verdict with a reason.

    python /app/scripts/explain_selection.py
    python /app/scripts/explain_selection.py --samples 25
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

from photo_importer import camera as cam
from photo_importer.config import default_config_path, load_config
from photo_importer.database import Database
from photo_importer.selection import ImportedIndex, _kb_variants, sizes_reported


def verdict(index: ImportedIndex, name: str, size_kb: int | None, names_only: bool) -> str:
    """Why this file would or would not be downloaded."""
    key = name.casefold()
    if names_only:
        return "skip: filename in history" if index.has_name(key) else "download: filename unknown"

    if key in index.sizeless_names:
        return "skip: filename in history (legacy row without size)"

    known = index.byte_sizes.get(key)
    if not known:
        return "download: filename not in history"
    if size_kb is None:
        return "download: listing reported no size, will hash after transfer"
    if any(size_kb in _kb_variants(size) for size in known):
        return "skip: filename + size match history"

    expected = sorted({kb for size in known for kb in _kb_variants(size)})
    return f"download: size {size_kb} KB does not match history {expected} KB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument(
        "--samples",
        type=int,
        default=10,
        help="How many per-file verdicts to print (default 10)",
    )
    parser.add_argument(
        "--raw-lines",
        type=int,
        default=5,
        help="How many raw gphoto2 listing lines to print (default 5)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config or default_config_path())

    detected = cam.detect_cameras(config.camera_name)
    if not detected:
        print(f"No camera matching {config.camera_name!r} found.", file=sys.stderr)
        return 1
    target = detected[0]
    print(f"Camera: {target.model} (port={target.port})\n")

    raw = cam.list_files_raw(target.port)
    listed = cam.parse_file_list(raw)
    if not listed:
        print("No files parsed from the listing. Raw output follows:\n")
        print(raw[:4000])
        return 1

    file_lines = [line for line in raw.splitlines() if line.strip().startswith("#")]
    print(f"--- raw listing ({len(file_lines)} file lines, showing {args.raw_lines}) ---")
    for line in file_lines[: args.raw_lines]:
        print(f"  {line}")

    with_sizes = sizes_reported(listed)
    names_only = with_sizes == 0
    print(f"\nParsed {len(listed)} files; {with_sizes} had a size in the listing.")
    if names_only:
        print("=> No sizes available, so matching falls back to filename only.")
        print("   If the lines above do show a size, its label is not 'KB' and")
        print("   camera.parse_file_list needs to learn this camera's format.")
    else:
        print("=> Matching on filename + size.")

    database = Database(config.database_path)
    index = database.imported_index()
    stats = database.stats()
    print(
        f"\nDatabase: {config.database_path}\n"
        f"  rows={stats.get('total_imports')} "
        f"distinct names with size={len(index.byte_sizes)} "
        f"legacy names without size={len(index.sizeless_names)}"
    )

    reasons = Counter()
    samples: list[str] = []
    for item in listed:
        why = verdict(index, item.name, item.size_kb, names_only)
        reasons[why.split(":")[0]] += 1
        if len(samples) < args.samples:
            size = f"{item.size_kb} KB" if item.size_kb is not None else "size unknown"
            samples.append(f"  {item.name:<20} {size:>12}  {why}")

    print(f"\n--- first {len(samples)} verdicts ---")
    print("\n".join(samples))

    print("\n--- totals ---")
    for reason, count in reasons.most_common():
        print(f"  {reason:<9} {count}")

    detailed = Counter()
    for item in listed:
        detailed[verdict(index, item.name, item.size_kb, names_only)] += 1
    print("\n--- reasons in detail ---")
    for reason, count in detailed.most_common(10):
        print(f"  {count:>6}  {reason}")

    print("\nNothing was downloaded or changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
