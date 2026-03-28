#!/usr/bin/env python3
"""Backfill missing motion resolution coordinates in data/motions.csv.

For each existing row in data/motions.csv where resolution_x/y/width/height are
blank, this script searches the row's resolution_page in the corresponding PDF
and attempts to locate the decision token bounding box.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, List

from extract_motion_resolutions import find_resolution_bbox

DEFAULT_INPUT = Path("data/motions.csv")
PDF_DIR = Path("data/annual_reports")


def has_coords(row: Dict[str, str]) -> bool:
    return all(
        (
            row.get("resolution_x", "").strip(),
            row.get("resolution_y", "").strip(),
            row.get("resolution_width", "").strip(),
            row.get("resolution_height", "").strip(),
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to motions CSV (default: data/motions.csv)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show counts only, do not write changes",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    csv_path = args.input

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    with csv_path.open("r", encoding="utf-8", newline="") as f:
        rows: List[Dict[str, str]] = list(csv.DictReader(f))

    if not rows:
        print("No rows found.")
        return 0

    fieldnames = list(rows[0].keys())
    bbox_cache_by_file: Dict[str, Dict[int, list]] = {}
    ocr_bbox_cache_by_file: Dict[str, Dict[int, list]] = {}

    updated = 0
    missing_after = 0

    for row in rows:
        if has_coords(row):
            continue

        file_name = row.get("file", "").strip()
        resolution = row.get("resolution", "").strip()
        page_raw = row.get("resolution_page", "").strip()

        if not file_name or not resolution or not page_raw.isdigit():
            missing_after += 1
            continue

        page = int(page_raw)
        pdf_path = PDF_DIR / file_name
        if not pdf_path.exists():
            missing_after += 1
            continue

        file_cache = bbox_cache_by_file.setdefault(file_name, {})
        file_ocr_cache = ocr_bbox_cache_by_file.setdefault(file_name, {})
        x, y, width, height = find_resolution_bbox(
            pdf_path,
            page,
            resolution,
            file_cache,
            file_ocr_cache,
        )

        if x and y and width and height:
            row["resolution_x"] = x
            row["resolution_y"] = y
            row["resolution_width"] = width
            row["resolution_height"] = height
            updated += 1
        else:
            missing_after += 1

    if args.dry_run:
        print(f"Would update {updated} row(s); {missing_after} row(s) would remain without coordinates.")
        return 0

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} row(s).")
    print(f"Rows still missing coordinates: {missing_after}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
