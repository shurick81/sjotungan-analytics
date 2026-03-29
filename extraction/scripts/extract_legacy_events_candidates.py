#!/usr/bin/env python3
"""Extract candidate legacy financial events (2011-2009) from scanned reports.

This script is intentionally conservative: it prints candidate rows in
`financial_events.csv` format so they can be reviewed before append.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import pytesseract
from pdf2image import convert_from_path

DPI = 300
CANVAS_SCALE = 72 / DPI * 1.5
STD_W = 100
STD_H = 20


@dataclass(frozen=True)
class Item:
    category_id: int
    amount: int
    page: int
    amount_key: str


@dataclass(frozen=True)
class Config:
    pdf: str
    min_left_px: int
    col_split_px: int
    items: Tuple[Item, ...]


CONFIGS: Dict[int, Config] = {
    2011: Config(
        pdf="arsredovisning2012.pdf",
        min_left_px=1500,
        col_split_px=1950,
        items=(
            Item(0, 35942348, 13, "35942348"),
            Item(1, -18334838, 13, "18334838"),
            Item(2, -3277255, 13, "3277255"),
            Item(6, -841472, 13, "841472"),
            Item(7, -4855123, 13, "4855123"),
            Item(15, 402398, 13, "402398"),
            Item(8, -5011350, 11, "5011350"),
            Item(9, -8108059, 11, "8108059"),
            Item(10, -1918493, 11, "1918493"),
            Item(3, -4912516, 11, "4912516"),
        ),
    ),
    2010: Config(
        pdf="arsredovisning2010.pdf",
        min_left_px=1500,
        col_split_px=1950,
        items=(
            Item(0, 36086268, 12, "36086268"),
            Item(1, -21830847, 12, "21830847"),
            Item(2, -4000006, 12, "4000006"),
            Item(6, -553322, 12, "553322"),
            Item(7, -3684655, 12, "3684655"),
            Item(15, 23846, 12, "23846"),
            Item(8, 3276443, 10, "3276443"),
            Item(3, -4984498, 10, "4984498"),
        ),
    ),
    2009: Config(
        pdf="arsredovisning_2009.pdf",
        min_left_px=1500,
        col_split_px=1950,
        items=(
            Item(0, 37503996, 12, "37503996"),
            Item(1, -21542990, 12, "21542990"),
            Item(2, -3073838, 12, "3073838"),
            Item(6, -595641, 12, "595641"),
            Item(7, -4913861, 12, "4913861"),
            Item(15, 203446, 12, "203446"),
            Item(8, -6943969, 10, "6943969"),
            Item(9, 2056237, 10, "2056237"),
            Item(10, 557227, 10, "557227"),
            Item(3, -4110026, 10, "4110026"),
        ),
    ),
}


def _strip_token(text: str) -> str:
    return (
        str(text)
        .replace(" ", "")
        .replace(",", "")
        .replace(".", "")
        .replace("-", "")
        .replace(":", "")
        .replace(";", "")
    )


def _find_amount_coords(data, amount_digits: str, min_left_px: int, col_split_px: int):
    digit_tokens = data[
        (data["text"].astype(str).str.contains(r"\d", na=False))
        & (data["left"] >= min_left_px)
    ].copy()

    if digit_tokens.empty:
        return None

    digit_tokens = digit_tokens.sort_values(["top", "left"])

    rows: List[list] = []
    current_row = []
    current_y = None

    for _, token in digit_tokens.iterrows():
        if current_y is None or abs(token["top"] - current_y) > 15:
            if current_row:
                rows.append(current_row)
            current_row = [token]
            current_y = token["top"]
        else:
            current_row.append(token)

    if current_row:
        rows.append(current_row)

    for row_tokens in rows:
        row_tokens.sort(key=lambda t: t["left"])

        current_col = [t for t in row_tokens if t["left"] < col_split_px]
        current_digits = "".join(_strip_token(t["text"]) for t in current_col)
        if amount_digits in current_digits and current_col:
            leftmost = current_col[0]
            return round(leftmost["left"] * CANVAS_SCALE), round(leftmost["top"] * CANVAS_SCALE)

        full_row_digits = "".join(_strip_token(t["text"]) for t in row_tokens)
        if amount_digits in full_row_digits:
            leftmost = row_tokens[0]
            return round(leftmost["left"] * CANVAS_SCALE), round(leftmost["top"] * CANVAS_SCALE)

    return None


def extract_year(year: int) -> List[str]:
    cfg = CONFIGS[year]

    page_cache = {}
    for item in cfg.items:
        if item.page in page_cache:
            continue
        img = convert_from_path(
            f"data/annual_reports/{cfg.pdf}",
            dpi=DPI,
            first_page=item.page,
            last_page=item.page,
        )[0]
        ocr = pytesseract.image_to_data(
            img,
            lang="swe",
            config="--psm 6",
            output_type=pytesseract.Output.DATAFRAME,
        )
        page_cache[item.page] = ocr.dropna(subset=["text"])

    lines: List[str] = []
    for item in cfg.items:
        coords = _find_amount_coords(
            page_cache[item.page],
            item.amount_key,
            cfg.min_left_px,
            cfg.col_split_px,
        )
        if coords is None:
            raise RuntimeError(
                f"Failed to locate amount {item.amount_key} for year {year}, category {item.category_id}."
            )

        x, y = coords
        lines.append(
            f"{year},{item.category_id},{item.amount},{cfg.pdf},{item.page},{x},{y},{STD_W},{STD_H}"
        )

    return lines


def _existing_keys(path: Path) -> set:
    if not path.exists():
        return set()
    keys = set()
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            keys.add((int(row["year"]), int(row["category_id"])))
    return keys


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract legacy financial event candidates")
    parser.add_argument("years", nargs="*", type=int, help="Years to extract (default: 2011 2010 2009)")
    parser.add_argument("--append", action="store_true", help="Append to data/financial_events.csv")
    args = parser.parse_args()

    years = args.years or [2011, 2010, 2009]
    for year in years:
        if year not in CONFIGS:
            raise SystemExit(f"No config for year {year}. Available: {sorted(CONFIGS)}")

    out_lines: List[str] = []
    for year in years:
        out_lines.extend(extract_year(year))

    if not args.append:
        for line in out_lines:
            print(line)
        return 0

    csv_path = Path("data/financial_events.csv")
    existing = _existing_keys(csv_path)
    new_lines = []
    for line in out_lines:
        year_s, cat_s, *_ = line.split(",", 2)
        key = (int(year_s), int(cat_s))
        if key in existing:
            continue
        new_lines.append(line)

    if not new_lines:
        print("No new rows to append.")
        return 0

    with csv_path.open("a", encoding="utf-8", newline="") as f:
        for line in new_lines:
            f.write(line + "\n")

    print(f"Appended {len(new_lines)} rows to {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
