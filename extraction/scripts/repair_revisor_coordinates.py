#!/usr/bin/env python3
"""Repair category 5 (Revisorer) bounding boxes in data/general_states.csv.

This script recalculates x/y/width/height for existing Revisorer rows by matching
the names in each row's value field against PDF word boxes on the recorded page.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


CSV_FIELDNAMES = ["year", "category_id", "value", "file", "page", "x", "y", "width", "height"]
GENERAL_STATES_PATH = Path("data/general_states.csv")
ANNUAL_REPORTS_DIR = Path("data/annual_reports")

WordBox = Tuple[float, float, float, float, str, str]


def run_command(command: List[str]) -> str:
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    return proc.stdout


def normalize_token(text: str) -> str:
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^0-9a-z]+", "", no_marks)


def extract_page_words_bbox(pdf_path: Path, page: int) -> List[WordBox]:
    xml = run_command(
        [
            "pdftotext",
            "-f",
            str(page),
            "-l",
            str(page),
            "-bbox-layout",
            str(pdf_path),
            "-",
        ]
    )

    pattern = re.compile(
        r'<word xMin="([0-9.]+)" yMin="([0-9.]+)" xMax="([0-9.]+)" yMax="([0-9.]+)">(.*?)</word>'
    )

    words: List[WordBox] = []
    for match in pattern.finditer(xml):
        x0, y0, x1, y1, raw = match.groups()
        decoded = html.unescape(raw).strip()
        if not decoded:
            continue
        words.append((float(x0), float(y0), float(x1), float(y1), decoded, normalize_token(decoded)))

    if words:
        return words

    # OCR fallback for scanned pages without text layer.
    with tempfile.TemporaryDirectory(prefix="repair_revisor_ocr_") as tmpdir:
        image_prefix = Path(tmpdir) / "page"
        dpi = 300
        run_command(
            [
                "pdftoppm",
                "-f",
                str(page),
                "-l",
                str(page),
                "-r",
                str(dpi),
                "-png",
                str(pdf_path),
                str(image_prefix),
            ]
        )

        image_files = sorted(Path(tmpdir).glob("page-*.png"))
        if not image_files:
            return words

        output_base = Path(tmpdir) / "ocr"
        run_command(
            [
                "tesseract",
                str(image_files[0]),
                str(output_base),
                "-l",
                "swe+eng",
                "--psm",
                "6",
                "tsv",
            ]
        )

        tsv_path = output_base.with_suffix(".tsv")
        if not tsv_path.exists():
            return words

        px_to_pt = 72.0 / float(dpi)
        tsv = tsv_path.read_text(encoding="utf-8", errors="ignore")
        lines = tsv.splitlines()
        if not lines:
            return words

        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 12:
                continue

            text = parts[11].strip()
            if not text:
                continue

            try:
                left = float(parts[6])
                top = float(parts[7])
                width = float(parts[8])
                height = float(parts[9])
                conf = float(parts[10])
            except ValueError:
                continue

            if conf < 0:
                continue

            x0 = left * px_to_pt
            y0 = top * px_to_pt
            x1 = (left + width) * px_to_pt
            y1 = (top + height) * px_to_pt
            words.append((x0, y0, x1, y1, text, normalize_token(text)))

    return words


def bbox_union(boxes: Sequence[Tuple[float, float, float, float]]) -> Optional[Tuple[float, float, float, float]]:
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def find_phrase_bbox(words: Sequence[WordBox], phrase: str) -> Optional[Tuple[float, float, float, float]]:
    tokens = [normalize_token(part) for part in phrase.split()]
    tokens = [token for token in tokens if token]
    if not tokens:
        return None

    # Exact contiguous token match first.
    for idx in range(len(words) - len(tokens) + 1):
        chunk = words[idx : idx + len(tokens)]
        if [word[5] for word in chunk] == tokens:
            return bbox_union([(word[0], word[1], word[2], word[3]) for word in chunk])

    # Fallback: all tokens found individually.
    hits = []
    for token in tokens:
        hit = next((word for word in words if word[5] == token), None)
        if not hit:
            return None
        hits.append((hit[0], hit[1], hit[2], hit[3]))
    return bbox_union(hits)


def name_variants(name: str) -> List[str]:
    variants = [name]
    variants.append(name.replace("BoRevision", "Bo Revision"))
    variants.append(name.replace("Borevision", "Bo Revision"))
    variants.append(name.replace("Kungsbron Borevision", "Kungsbron Bo Revision"))
    # Deduplicate while preserving order.
    seen = set()
    result: List[str] = []
    for variant in variants:
        key = variant.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(key)
    return result


def bbox_to_csv(box: Optional[Tuple[float, float, float, float]]) -> Tuple[str, str, str, str]:
    if box is None:
        return "", "", "", ""
    x0, y0, x1, y1 = box
    return (
        str(int(round(x0))),
        str(int(round(y0))),
        str(int(round(max(1.0, x1 - x0)))),
        str(int(round(max(1.0, y1 - y0)))),
    )


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return [row for row in csv.DictReader(handle)]


def write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDNAMES})


def recompute_revisor_box(value: str, words: Sequence[WordBox]) -> Optional[Tuple[float, float, float, float]]:
    names = [part.strip() for part in value.split(";") if part.strip()]
    boxes = []
    for name in names:
        box = None
        for variant in name_variants(name):
            box = find_phrase_bbox(words, variant)
            if box is not None:
                break
        if box is not None:
            boxes.append(box)
    return bbox_union(boxes)


def main() -> int:
    parser = argparse.ArgumentParser(description="Repair Revisorer coordinates in general_states.csv")
    parser.add_argument("--apply", action="store_true", help="Write updated coordinates to CSV")
    args = parser.parse_args()

    rows = load_rows(GENERAL_STATES_PATH)
    updated = 0
    skipped = 0

    for row in rows:
        if row.get("category_id", "") != "5":
            continue

        pdf_name = row.get("file", "")
        page_text = row.get("page", "")
        value = row.get("value", "")
        if not pdf_name or not page_text or not value:
            skipped += 1
            continue

        pdf_path = ANNUAL_REPORTS_DIR / pdf_name
        if not pdf_path.exists():
            skipped += 1
            continue

        try:
            page = int(page_text)
        except ValueError:
            skipped += 1
            continue

        words = extract_page_words_bbox(pdf_path, page)
        if not words:
            skipped += 1
            continue

        box = recompute_revisor_box(value, words)
        if box is None:
            skipped += 1
            continue

        x, y, width, height = bbox_to_csv(box)
        row["x"] = x
        row["y"] = y
        row["width"] = width
        row["height"] = height
        updated += 1

    print(f"updated={updated} skipped={skipped}")

    if args.apply:
        write_rows(GENERAL_STATES_PATH, rows)
        print(f"wrote {GENERAL_STATES_PATH}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
