#!/usr/bin/env python3
"""Extract equity/assets state rows used for soliditet.

The script derives the correct year column from already-known debt rows in
data/financial_states.csv, then extracts:

- category 6: Eget kapital
- category 7: Summa tillgångar

It prints CSV candidate rows by default, and can append missing rows to
data/financial_states.csv when --append is used.
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pytesseract
from pdf2image import convert_from_path
from PyPDF2 import PdfReader

STATES_PATH = Path("data/financial_states.csv")
OUTPUT_PATH = Path("data/financial_states.csv")
ANNUAL_REPORTS_DIR = Path("data/annual_reports")

CAT_EQUITY = "6"
CAT_ASSETS = "7"
MIN_REASONABLE_AMOUNT = 100_000
MAX_REASONABLE_AMOUNT = 2_000_000_000

DPI = 300
CANVAS_SCALE = 72 / DPI * 1.5
STD_W = 100
STD_H = 20

CSV_FIELDS = ["year", "category_id", "amount", "file", "page", "x", "y", "width", "height"]

DEBT_LINE_ANCHORS = (
    "skulder till kreditinstitut",
    "skulder kreditinstitut till",
    "langfristiga skulder till kreditinstitut",
    "langfristiga skulder exklusive kortfristig del",
)
EQUITY_LINE_ANCHORS = (
    "summa eget kapital",
    "summa kapital eget",
)
TOTAL_LINE_ANCHORS = (
    "summa eget kapital och skulder",
    "summa kapital och skulder eget",
    "summa tillgangar",
    "sumrna tillgangar",
)

EQUITY_KEYWORD_GROUPS = (
    ("summa", "eget", "kapital"),
    ("summa", "eget", "kap"),
)

TOTAL_KEYWORD_GROUPS = (
    ("summa", "tillg"),
    ("summa", "kapital", "skulder"),
    ("summa", "kap", "skulder"),
)


@dataclass(frozen=True)
class YearLayout:
    min_left_px: int
    col_split_px: int
    source_column: str = "current"
    psm: int = 6
    fixed_column_index: Optional[int] = None
    equity_pages: Tuple[int, ...] = ()
    total_pages: Tuple[int, ...] = ()
    full_scan: bool = False


YEAR_LAYOUTS: Dict[str, YearLayout] = {
    "2006": YearLayout(1200, 1850, source_column="previous", equity_pages=(7,), total_pages=(7,)),
    "2007": YearLayout(1200, 1850, source_column="previous", equity_pages=(8,), total_pages=(8,)),
    "2008": YearLayout(1200, 1850, source_column="current", equity_pages=(8,), total_pages=(8,)),
    "2009": YearLayout(
        1500,
        1950,
        source_column="current",
        fixed_column_index=0,
        equity_pages=(9, 8),
        total_pages=(9, 8),
    ),
    "2010": YearLayout(1500, 1950, source_column="current", equity_pages=(14,), total_pages=(14,)),
    "2011": YearLayout(1500, 1950, source_column="current", equity_pages=(15, 10), total_pages=(10, 15)),
    "2014": YearLayout(
        1600,
        1950,
        source_column="current",
        fixed_column_index=0,
        equity_pages=(9, 8),
        total_pages=(9, 8),
    ),
    "2015": YearLayout(1600, 1950, source_column="previous", equity_pages=(11, 10), total_pages=(11, 10)),
    "2016": YearLayout(
        1600,
        1950,
        source_column="previous",
        fixed_column_index=1,
        equity_pages=(8, 7),
        total_pages=(8, 7),
    ),
    "2017": YearLayout(1600, 1950, source_column="previous", equity_pages=(8, 7), total_pages=(8, 7)),
}


def normalize_text(text: str) -> str:
    value = (text or "").lower()
    value = value.replace("å", "a").replace("ä", "a").replace("ö", "o")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def strip_digits(text: str) -> str:
    return re.sub(r"\D", "", text or "")


def is_plausible_amount(value: Optional[int]) -> bool:
    if value is None:
        return False
    return 1_000_000 <= value <= MAX_REASONABLE_AMOUNT


def _partition_amount_tokens(
    tokens: Sequence[str],
    preferred: Optional[int] = None,
    index: int = 0,
) -> Tuple[Tuple[int, int, int], List[int]]:
    if index >= len(tokens):
        return (0, 0, 0), []

    if len(tokens[index]) <= 2:
        best_score, best_amounts = _partition_amount_tokens(tokens, preferred, index + 1)
    else:
        best_score, best_amounts = ((-1, -1, -999), [])

    for width in range(1, 5):
        end = index + width
        if end > len(tokens):
            break
        segment = tokens[index:end]
        if any(not token for token in segment):
            continue
        if any(len(token) > 3 for token in segment):
            continue
        if width > 1 and any(len(token) != 3 for token in segment[1:]):
            continue

        amount = int("".join(segment))
        if not is_plausible_amount(amount):
            continue

        tail_score, tail_amounts = _partition_amount_tokens(tokens, preferred, end)
        preferred_hits = (1 if preferred is not None and amount == preferred else 0) + tail_score[0]
        digits_covered = len("".join(segment)) + tail_score[1]
        amount_count = 1 + tail_score[2]
        score = (preferred_hits, digits_covered, -amount_count)
        if score > best_score:
            best_score = score
            best_amounts = [amount] + tail_amounts

    return best_score, best_amounts


def parse_line_amounts(text: str, preferred: Optional[int] = None) -> List[int]:
    raw_tokens = [strip_digits(token) for token in re.findall(r"\d+", text or "")]
    raw_tokens = [token for token in raw_tokens if token]

    digit_tokens: List[str] = []
    for token in raw_tokens:
        if len(token) <= 3:
            digit_tokens.append(token)
            continue

        # OCR often merges separators, e.g. "191564" for "191 564".
        head = len(token) % 3
        if head:
            digit_tokens.append(token[:head])
        for idx in range(head, len(token), 3):
            digit_tokens.append(token[idx:idx + 3])

    _, amounts = _partition_amount_tokens(digit_tokens, preferred)
    return amounts


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def existing_keys(states: List[Dict[str, str]]) -> set:
    keys = set()
    for row in states:
        year = (row.get("year") or "").strip()
        category_id = (row.get("category_id") or "").strip()
        if year and category_id:
            keys.add((year, category_id))
    return keys


def build_row(year: str, category_id: str, amount: int, file_name: str, page: int, x: int, y: int) -> Dict[str, str]:
    return {
        "year": year,
        "category_id": category_id,
        "amount": str(amount),
        "file": file_name,
        "page": str(page),
        "x": str(x),
        "y": str(y),
        "width": str(STD_W),
        "height": str(STD_H),
    }


def append_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        for row in rows:
            writer.writerow(row)


def group_ocr_rows(dataframe) -> List[List[dict]]:
    tokens = dataframe.dropna(subset=["text"]).sort_values(["top", "left"])
    rows: List[List[dict]] = []
    current: List[dict] = []
    current_y: Optional[int] = None
    for _, token in tokens.iterrows():
        token_dict = token.to_dict()
        top = int(token_dict["top"])
        if current_y is None or abs(top - current_y) > 15:
            if current:
                rows.append(current)
            current = [token_dict]
            current_y = top
        else:
            current.append(token_dict)
    if current:
        rows.append(current)
    return rows


def extract_page_ocr_rows(pdf_path: Path, page_number: int, psm: int = 6) -> List[List[dict]]:
    image = convert_from_path(
        str(pdf_path),
        dpi=DPI,
        first_page=page_number,
        last_page=page_number,
    )[0]
    data = pytesseract.image_to_data(
        image,
        lang="swe",
        config=f"--psm {psm}",
        output_type=pytesseract.Output.DATAFRAME,
    )
    return group_ocr_rows(data)


def get_cached_ocr_rows(
    pdf_path: Path,
    page_number: int,
    cache: Dict[Tuple[int, int], List[List[dict]]],
    psm: int = 6,
) -> List[List[dict]]:
    key = (page_number, psm)
    if key not in cache:
        cache[key] = extract_page_ocr_rows(pdf_path, page_number, psm=psm)
    return cache[key]


def strip_token(text: str) -> str:
    return (
        str(text)
        .replace(" ", "")
        .replace(",", "")
        .replace(".", "")
        .replace("-", "")
        .replace(":", "")
        .replace(";", "")
    )


def choose_amount_from_ocr_rows(
    rows: Sequence[Sequence[dict]],
    anchors: Sequence[str],
    column_index: int,
    excludes: Sequence[str] = (),
    keyword_groups: Sequence[Sequence[str]] = (),
    min_left_px: int = 0,
    col_split_px: Optional[int] = None,
    source_column: str = "current",
) -> Optional[int]:
    for row in rows:
        row_sorted = sorted(row, key=lambda it: int(it.get("left", 0)))
        if not row_sorted:
            continue

        line = " ".join(str(token.get("text", "")) for token in row_sorted).strip()
        norm = normalize_text(line)
        if excludes and any(ex in norm for ex in excludes):
            continue

        if not (any(anchor in norm for anchor in anchors) or line_matches_keywords(line, keyword_groups)):
            continue

        # First parse on the full OCR row text; this is often more stable than
        # column-cropped token streams for noisy scans.
        full_line_amount = choose_amount_for_year(parse_line_amounts(line), column_index)
        if full_line_amount is not None and is_plausible_amount(full_line_amount):
            return full_line_amount

        filtered = [t for t in row_sorted if int(t.get("left", 0)) >= min_left_px]
        if not filtered:
            filtered = row_sorted

        token_groups: List[Sequence[dict]] = [filtered]
        if col_split_px is not None:
            left_col = [t for t in filtered if int(t.get("left", 0)) < col_split_px]
            right_col = [t for t in filtered if int(t.get("left", 0)) >= col_split_px]
            preferred = left_col
            secondary = right_col
            if source_column == "previous":
                preferred, secondary = secondary, preferred
            token_groups = [preferred, secondary, filtered]

        for group in token_groups:
            if not group:
                continue
            text = " ".join(str(t.get("text", "")) for t in sorted(group, key=lambda it: int(it.get("left", 0))))
            digits = strip_token(text)
            if not digits:
                continue
            amounts = parse_line_amounts(digits)
            amount = choose_amount_for_year(amounts, column_index)
            if amount is not None and is_plausible_amount(amount):
                return amount

    return None


def find_token_span(tokens: Sequence[dict], amount: int) -> Optional[Tuple[int, int]]:
    target = str(amount)
    digit_tokens = [strip_digits(str(token.get("text", ""))) for token in tokens]
    for start in range(len(digit_tokens)):
        if not digit_tokens[start]:
            continue
        combined = ""
        for end in range(start, len(digit_tokens)):
            piece = digit_tokens[end]
            if not piece:
                continue
            combined += piece
            if combined == target:
                return start, end
            if len(combined) >= len(target):
                break
    return None


def coords_for_amount(rows: Sequence[Sequence[dict]], amount: int) -> Optional[Tuple[int, int]]:
    for row in rows:
        span = find_token_span(row, amount)
        if span is None:
            continue
        start, _ = span
        token = row[start]
        return round(int(token["left"]) * CANVAS_SCALE), round(int(token["top"]) * CANVAS_SCALE)
    return None


def text_lines_from_page(reader: PdfReader, page_number: int) -> List[str]:
    text = reader.pages[page_number - 1].extract_text() or ""
    return [line.strip() for line in text.splitlines() if line.strip()]


def ocr_lines_from_rows(rows: Sequence[Sequence[dict]]) -> List[str]:
    return [" ".join(str(token.get("text", "")) for token in row).strip() for row in rows]


def first_matching_line(lines: Iterable[str], anchors: Sequence[str]) -> Optional[str]:
    for line in lines:
        norm = normalize_text(line)
        if any(anchor in norm for anchor in anchors):
            return line
    return None


def first_matching_line_with_excludes(
    lines: Iterable[str],
    anchors: Sequence[str],
    excludes: Sequence[str],
) -> Optional[str]:
    for line in lines:
        norm = normalize_text(line)
        if excludes and any(ex in norm for ex in excludes):
            continue
        if any(anchor in norm for anchor in anchors):
            return line
    return None


def line_matches_keywords(line: str, keyword_groups: Sequence[Sequence[str]]) -> bool:
    if not keyword_groups:
        return False
    norm = normalize_text(line)
    return any(all(keyword in norm for keyword in group) for group in keyword_groups)


def choose_amount_from_lines(
    lines: Sequence[str],
    anchors: Sequence[str],
    column_index: int,
    excludes: Sequence[str] = (),
    keyword_groups: Sequence[Sequence[str]] = (),
) -> Optional[int]:
    exact_match_amounts: List[int] = []
    fuzzy_match_amounts: List[int] = []

    for line in lines:
        norm = normalize_text(line)
        if excludes and any(ex in norm for ex in excludes):
            continue

        amounts = parse_line_amounts(line)
        if not amounts:
            continue

        amount = choose_amount_for_year(amounts, column_index)
        if amount is None or not is_plausible_amount(amount):
            continue

        if any(anchor in norm for anchor in anchors):
            exact_match_amounts.append(amount)
        elif line_matches_keywords(line, keyword_groups):
            fuzzy_match_amounts.append(amount)

    if exact_match_amounts:
        return exact_match_amounts[0]
    if fuzzy_match_amounts:
        return fuzzy_match_amounts[0]
    return None


def coords_for_anchor_line(
    rows: Sequence[Sequence[dict]],
    anchors: Sequence[str],
    excludes: Sequence[str] = (),
    keyword_groups: Sequence[Sequence[str]] = (),
    min_left_px: int = 0,
) -> Optional[Tuple[int, int]]:
    for row in rows:
        filtered = [token for token in row if int(token.get("left", 0)) >= min_left_px]
        if not filtered:
            continue
        line = " ".join(str(token.get("text", "")) for token in filtered).strip()
        norm = normalize_text(line)
        if excludes and any(ex in norm for ex in excludes):
            continue
        if not (any(anchor in norm for anchor in anchors) or line_matches_keywords(line, keyword_groups)):
            continue
        first = filtered[0]
        return round(int(first["left"]) * CANVAS_SCALE), round(int(first["top"]) * CANVAS_SCALE)
    return None


def candidate_pages_from_text(reader: PdfReader, anchors: Sequence[str]) -> List[int]:
    pages: List[int] = []
    for page_number, page in enumerate(reader.pages, start=1):
        norm = normalize_text(page.extract_text() or "")
        if any(anchor in norm for anchor in anchors):
            pages.append(page_number)
    return pages


def choose_amount_for_year(amounts: Sequence[int], column_index: int) -> Optional[int]:
    plausible = [amount for amount in amounts if is_plausible_amount(amount)]
    if not plausible:
        return None
    if column_index < len(plausible):
        return plausible[column_index]
    return plausible[0]


def find_column_index(
    reader: PdfReader,
    pdf_path: Path,
    page_numbers: Sequence[int],
    known_debt_amount: int,
    ocr_cache: Dict[Tuple[int, int], List[List[dict]]],
    psm: int = 6,
) -> Optional[int]:
    for page_number in page_numbers:
        text_line = first_matching_line(text_lines_from_page(reader, page_number), DEBT_LINE_ANCHORS)
        if text_line:
            amounts = parse_line_amounts(text_line, preferred=known_debt_amount)
            if known_debt_amount in amounts:
                return amounts.index(known_debt_amount)

        ocr_rows = get_cached_ocr_rows(pdf_path, page_number, ocr_cache, psm=psm)
        ocr_line = first_matching_line(ocr_lines_from_rows(ocr_rows), DEBT_LINE_ANCHORS)
        if ocr_line:
            amounts = parse_line_amounts(ocr_line, preferred=known_debt_amount)
            if known_debt_amount in amounts:
                return amounts.index(known_debt_amount)

        # Some PDFs split labels and numbers across nearby lines.
        ocr_all = ocr_lines_from_rows(ocr_rows)
        for line in ocr_all:
            amounts = parse_line_amounts(line, preferred=known_debt_amount)
            if known_debt_amount in amounts:
                return amounts.index(known_debt_amount)

    # Fallback: assume first amount column is current year when no reliable match.
    return 0


def extract_target_from_pages(
    reader: PdfReader,
    pdf_path: Path,
    page_numbers: Sequence[int],
    anchors: Sequence[str],
    column_index: int,
    ocr_cache: Dict[Tuple[int, int], List[List[dict]]],
    excludes: Sequence[str] = (),
    keyword_groups: Sequence[Sequence[str]] = (),
    min_left_px: int = 0,
    col_split_px: Optional[int] = None,
    source_column: str = "current",
    psm: int = 6,
) -> Optional[Tuple[int, int, int, int]]:
    for page_number in page_numbers:
        amount: Optional[int] = None
        ocr_rows = get_cached_ocr_rows(pdf_path, page_number, ocr_cache, psm=psm)
        amount = choose_amount_from_ocr_rows(
            ocr_rows,
            anchors,
            column_index,
            excludes=excludes,
            keyword_groups=keyword_groups,
            min_left_px=min_left_px,
            col_split_px=col_split_px,
            source_column=source_column,
        )

        if amount is None:
            text_lines = text_lines_from_page(reader, page_number)
            amount = choose_amount_from_lines(
                text_lines,
                anchors,
                column_index,
                excludes=excludes,
                keyword_groups=keyword_groups,
            )

        if amount is None or not is_plausible_amount(amount):
            continue

        coords = coords_for_amount(ocr_rows, amount)
        if coords is None:
            coords = coords_for_anchor_line(
                ocr_rows,
                anchors,
                excludes=excludes,
                keyword_groups=keyword_groups,
                min_left_px=min_left_px,
            )
        if coords is None:
            coords = (600, 420)
        x, y = coords
        return amount, page_number, x, y

    return None


def page_search_plan(reader: PdfReader, debt_page: int) -> Tuple[List[int], List[int]]:
    total_pages = len(reader.pages)
    nearby: List[int] = []
    for candidate in [debt_page - 1, debt_page, debt_page + 1, debt_page - 2, debt_page + 2]:
        if 1 <= candidate <= total_pages and candidate not in nearby:
            nearby.append(candidate)

    equity_pages = candidate_pages_from_text(reader, EQUITY_LINE_ANCHORS)
    total_pages_found = candidate_pages_from_text(reader, TOTAL_LINE_ANCHORS)

    ordered_equity = equity_pages + [page for page in nearby if page not in equity_pages]
    ordered_total = total_pages_found + [page for page in nearby if page not in total_pages_found]
    return ordered_equity, ordered_total


def append_ocr_anchor_pages(
    reader: PdfReader,
    pdf_path: Path,
    pages: List[int],
    anchors: Sequence[str],
    excludes: Sequence[str],
    ocr_cache: Dict[Tuple[int, int], List[List[dict]]],
    psm: int = 6,
) -> List[int]:
    seen = set(pages)
    for page_number in range(1, len(reader.pages) + 1):
        if page_number in seen:
            continue
        ocr_rows = get_cached_ocr_rows(pdf_path, page_number, ocr_cache, psm=psm)
        line = first_matching_line_with_excludes(ocr_lines_from_rows(ocr_rows), anchors, excludes)
        if line:
            pages.append(page_number)
            seen.add(page_number)
    return pages


def year_context(states: List[Dict[str, str]], year: str) -> Optional[Tuple[str, int, int]]:
    debt_rows = [row for row in states if row.get("year") == year and row.get("category_id") in {"0", "1"}]
    if not debt_rows:
        return None
    debt_rows.sort(key=lambda row: (0 if row.get("category_id") == "1" else 1))
    chosen = debt_rows[0]
    file_name = (chosen.get("file") or "").strip()
    page = int((chosen.get("page") or "0").strip())
    amount = int((chosen.get("amount") or "0").strip())
    return file_name, page, amount


def extract_year(states: List[Dict[str, str]], year: str) -> List[Dict[str, str]]:
    context = year_context(states, year)
    if context is None:
        print(f"WARN year={year}: no debt context in financial_states")
        return []

    file_name, debt_page, known_debt_amount = context
    pdf_path = ANNUAL_REPORTS_DIR / file_name
    if not pdf_path.exists():
        print(f"WARN year={year}: missing PDF {pdf_path}")
        return []

    reader = PdfReader(str(pdf_path))
    layout = YEAR_LAYOUTS.get(year)
    min_left_px = layout.min_left_px if layout else 1200
    col_split_px = layout.col_split_px if layout else None
    source_column = layout.source_column if layout else "current"
    psm = layout.psm if layout else 6

    ocr_cache: Dict[Tuple[int, int], List[List[dict]]] = {}
    debt_scan_pages: List[int] = []
    for page_number in [debt_page - 1, debt_page, debt_page + 1]:
        if 1 <= page_number <= len(reader.pages) and page_number not in debt_scan_pages:
            debt_scan_pages.append(page_number)

    column_index = layout.fixed_column_index if layout and layout.fixed_column_index is not None else None
    if column_index is None:
        column_index = find_column_index(
            reader,
            pdf_path,
            debt_scan_pages,
            known_debt_amount,
            ocr_cache,
            psm=psm,
        )
    if column_index is None:
        print(f"WARN year={year}: could not determine source column from debt rows in {file_name}")
        return []

    if layout and (layout.equity_pages or layout.total_pages):
        equity_pages = [p for p in layout.equity_pages if 1 <= p <= len(reader.pages)]
        total_pages = [p for p in layout.total_pages if 1 <= p <= len(reader.pages)]
    else:
        equity_pages, total_pages = page_search_plan(reader, debt_page)

    if not layout or layout.full_scan:
        equity_pages = append_ocr_anchor_pages(
            reader,
            pdf_path,
            equity_pages,
            EQUITY_LINE_ANCHORS,
            excludes=("och skulder",),
            ocr_cache=ocr_cache,
            psm=psm,
        )
        total_pages = append_ocr_anchor_pages(
            reader,
            pdf_path,
            total_pages,
            TOTAL_LINE_ANCHORS,
            excludes=(),
            ocr_cache=ocr_cache,
            psm=psm,
        )

    if not equity_pages:
        equity_pages = [p for p in [debt_page - 1, debt_page, debt_page + 1] if 1 <= p <= len(reader.pages)]
    if not total_pages:
        total_pages = [p for p in [debt_page - 1, debt_page, debt_page + 1] if 1 <= p <= len(reader.pages)]

    equity = extract_target_from_pages(
        reader,
        pdf_path,
        equity_pages,
        EQUITY_LINE_ANCHORS,
        column_index,
        ocr_cache,
        excludes=("och skulder",),
        keyword_groups=EQUITY_KEYWORD_GROUPS,
        min_left_px=min_left_px,
        col_split_px=col_split_px,
        source_column=source_column,
        psm=psm,
    )
    total = extract_target_from_pages(
        reader,
        pdf_path,
        total_pages,
        TOTAL_LINE_ANCHORS,
        column_index,
        ocr_cache,
        keyword_groups=TOTAL_KEYWORD_GROUPS,
        min_left_px=min_left_px,
        col_split_px=col_split_px,
        source_column=source_column,
        psm=psm,
    )

    if total is not None:
        total_amount = total[0]
        # Assets should not be lower than debt and should stay in a realistic range.
        if total_amount < known_debt_amount or total_amount > known_debt_amount * 4:
            total = None

    if equity is not None and total is not None:
        equity_amount = equity[0]
        total_amount = total[0]
        # Equity must be positive and no larger than total assets.
        if equity_amount <= 0 or equity_amount > total_amount:
            equity = None

    output: List[Dict[str, str]] = []
    if equity is None:
        print(f"WARN year={year}: no equity row found in {file_name}")
    else:
        amount, page_number, x, y = equity
        output.append(build_row(year, CAT_EQUITY, amount, file_name, page_number, x, y))

    if total is None:
        print(f"WARN year={year}: no assets/total row found in {file_name}")
    else:
        amount, page_number, x, y = total
        output.append(build_row(year, CAT_ASSETS, amount, file_name, page_number, x, y))

    return output


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract soliditet state candidates from annual reports")
    parser.add_argument("years", nargs="*", type=int, help="Years to process (default: all years in financial_states)")
    parser.add_argument("--append", action="store_true", help="Append missing rows to data/financial_states.csv")
    args = parser.parse_args()

    states = load_rows(STATES_PATH)
    years_available = sorted({row.get("year", "") for row in states if row.get("year")}, key=int)
    selected_years = [str(year) for year in args.years] if args.years else years_available

    out_rows: List[Dict[str, str]] = []
    for year in selected_years:
        out_rows.extend(extract_year(states, year))

    if not args.append:
        writer = csv.DictWriter(__import__("sys").stdout, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(out_rows)
        return 0

    existing = existing_keys(states)
    new_rows = [row for row in out_rows if (row["year"], row["category_id"]) not in existing]
    if not new_rows:
        print("No new rows to append.")
        return 0

    append_rows(OUTPUT_PATH, new_rows)
    print(f"Appended {len(new_rows)} rows to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
