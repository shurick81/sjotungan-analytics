#!/usr/bin/env python3
"""Extract annual-meeting attendance from protocol PDFs into general_states.

Targets wording in the voting-roll section, for example:
"Antal narvarande rostberattigade var 100 medlemmar samt 4 fullmakter."

Upserts directly into data/general_states.csv with categories:
- 6: Narvarande rosterattigade medlemmar
- 7: Fullmakter
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple


DEFAULT_OUTPUT = Path("data/general_states.csv")
PDF_DIR = Path("data/stamma_protocols")
CATEGORY_MEMBERS = "6"
CATEGORY_PROXIES = "7"
CSV_FIELDNAMES = ["year", "category_id", "value", "file", "page", "x", "y", "width", "height"]


WordBox = Tuple[float, float, float, float, str, str]


def run_command(command: List[str]) -> str:
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Required command not found: pdftotext or pdfinfo") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{stderr}") from exc
    return proc.stdout


def get_pdf_page_count(pdf_path: Path) -> int:
    output = run_command(["pdfinfo", str(pdf_path)])
    match = re.search(r"^Pages:\s+(\d+)", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not determine page count for {pdf_path}")
    return int(match.group(1))


def extract_page_text(pdf_path: Path, page: int) -> str:
    return run_command(
        [
            "pdftotext",
            "-f",
            str(page),
            "-l",
            str(page),
            str(pdf_path),
            "-",
        ]
    )


def normalize_text(text: str) -> str:
    lowered = text.lower().replace("\u00ad", "")
    # Join OCR line-wrap hyphenation, e.g. "medlem-\nmar" -> "medlemmar".
    lowered = re.sub(r"(?<=\w)-\s+(?=\w)", "", lowered)
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_marks).strip()


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

    word_pattern = re.compile(
        r'<word xMin="([0-9.]+)" yMin="([0-9.]+)" xMax="([0-9.]+)" yMax="([0-9.]+)">(.*?)</word>'
    )

    words: List[WordBox] = []
    for m in word_pattern.finditer(xml):
        x0, y0, x1, y1, raw_word = m.groups()
        decoded = html.unescape(raw_word).strip()
        if not decoded:
            continue
        words.append((float(x0), float(y0), float(x1), float(y1), decoded, normalize_token(decoded)))
    return words


def extract_page_text_ocr_and_words(pdf_path: Path, page: int, dpi: int = 300) -> Tuple[str, List[WordBox]]:
    with tempfile.TemporaryDirectory(prefix="attendance_ocr_") as tmpdir:
        image_prefix = Path(tmpdir) / "page"

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
            raise RuntimeError(f"OCR rasterization produced no image for {pdf_path.name} page {page}")
        image_path = image_files[0]
        output_base = Path(tmpdir) / "ocr"

        run_command(
            [
                "tesseract",
                str(image_path),
                str(output_base),
                "-l",
                "swe+eng",
                "--psm",
                "6",
                "txt",
            ]
        )
        run_command(
            [
                "tesseract",
                str(image_path),
                str(output_base),
                "-l",
                "swe+eng",
                "--psm",
                "6",
                "tsv",
            ]
        )

        txt_path = output_base.with_suffix(".txt")
        tsv_path = output_base.with_suffix(".tsv")
        ocr_text = txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path.exists() else ""
        tsv = tsv_path.read_text(encoding="utf-8", errors="ignore") if tsv_path.exists() else ""

    px_to_pt = 72.0 / float(dpi)
    words: List[WordBox] = []

    lines = tsv.splitlines()
    if not lines:
        return ocr_text, words

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

    return ocr_text, words


def find_attendance_in_text(text: str) -> Optional[Tuple[int, int, str, bool]]:
    normalized = normalize_text(text)

    patterns = [
        re.compile(
            r"antal\s+narvarande\s+r(?:o|e)stber[a-z]+(?:\s+medlemmar)?\s+var\s+(\d+)\s+medlemmar(?:\s+samt\s+(\d+)\s+fullmakt(?:er)?)?"
        ),
        re.compile(
            r"antal\s+(?:narvarande|postr(?:o|e)stande)\s+r(?:o|e)stber[a-z]+(?:\s+medlemmar)?\s+(?:var\s+)?(\d+)\s+medlemmar(?:\s+samt\s+(\d+)\s+fullmakt(?:er)?)?"
        ),
        re.compile(
            r"narvarande\s+r(?:o|e)stber[a-z]+\s+var\s+(\d+)\s+medlemmar(?:\s+samt\s+(\d+)\s+fullmakt(?:er)?)?"
        ),
        re.compile(
            r"postr(?:o|e)stande\s+r(?:o|e)stber[a-z]+\s+(?:var\s+)?(\d+)\s+medlemmar(?:\s+samt\s+(\d+)\s+fullmakt(?:er)?)?"
        ),
        re.compile(
            r"antal\s+r(?:o|e)stber[a-z]+\s+var\s+(\d+)\s+medlemmar\s*,?\s*varav\s+(\d+)\s+medlem(?:mar)?\s+med\s+fullmakt(?:er)?"
        ),
        re.compile(
            r"antal\s*:?\s*(\d+)\s+medlemmar\s+med\s+rostratt\s*,?\s*varav\s+(\d+)\s+medlem(?:mar)?\s+med\s+fullmakt(?:er)?"
        ),
        re.compile(r"(\d+)\s+medlemmar\s+samt\s+(\d+)\s+fullmakt(?:er)?"),
    ]

    for pattern in patterns:
        match = pattern.search(normalized)
        if match:
            members = int(match.group(1))
            has_proxies = bool(match.lastindex and match.group(2))
            proxies = int(match.group(2)) if has_proxies else 0
            return members, proxies, match.group(0), has_proxies
    return None


def extract_attendance(pdf_path: Path) -> Tuple[int, int, int, str, bool, List[WordBox], str]:
    page_count = get_pdf_page_count(pdf_path)
    for page in range(1, page_count + 1):
        page_text = extract_page_text(pdf_path, page)
        found = find_attendance_in_text(page_text)
        if found:
            members, proxies, evidence, has_proxies = found
            words = extract_page_words_bbox(pdf_path, page)
            return page, members, proxies, evidence, has_proxies, words, "text"

        # Fallback for scanned/image-based pages without text layer.
        ocr_text, ocr_words = extract_page_text_ocr_and_words(pdf_path, page)
        found_ocr = find_attendance_in_text(ocr_text)
        if found_ocr:
            members, proxies, evidence, has_proxies = found_ocr
            return page, members, proxies, evidence, has_proxies, ocr_words, "ocr"

    raise RuntimeError(f"No attendance phrase found in {pdf_path.name}")


def bbox_to_csv(box: Optional[Tuple[float, float, float, float]]) -> Tuple[str, str, str, str]:
    if not box:
        return "", "", "", ""
    x0, y0, x1, y1 = box
    return (
        str(int(round(x0))),
        str(int(round(y0))),
        str(int(round(x1 - x0))),
        str(int(round(y1 - y0))),
    )


def find_number_boxes(
    words: List[WordBox],
    members: int,
    proxies: int,
    has_proxies: bool,
) -> Tuple[Optional[Tuple[float, float, float, float]], Optional[Tuple[float, float, float, float]]]:
    members_token = str(members)
    proxies_token = str(proxies)

    member_idxs = [i for i, w in enumerate(words) if w[5] == members_token]
    proxy_idxs = [i for i, w in enumerate(words) if w[5] == proxies_token] if has_proxies else []
    fullmakt_idxs = [i for i, w in enumerate(words) if w[5].startswith("fullmakt")]

    proxy_idx: Optional[int] = None
    if fullmakt_idxs and proxy_idxs:
        full_idx = fullmakt_idxs[0]
        candidates = [i for i in proxy_idxs if i < full_idx and (full_idx - i) <= 6 and abs(words[full_idx][1] - words[i][1]) <= 8]
        if candidates:
            proxy_idx = candidates[-1]
    if proxy_idx is None and proxy_idxs:
        proxy_idx = proxy_idxs[0]

    member_idx: Optional[int] = None
    if proxy_idx is not None and member_idxs:
        candidates = [i for i in member_idxs if i < proxy_idx and (proxy_idx - i) <= 16 and abs(words[proxy_idx][1] - words[i][1]) <= 24]
        if candidates:
            member_idx = candidates[-1]
    if member_idx is None and member_idxs:
        member_idx = member_idxs[0]

    member_box = words[member_idx][:4] if member_idx is not None else None
    proxy_box = words[proxy_idx][:4] if proxy_idx is not None else None
    return member_box, proxy_box


def load_existing_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: List[Dict[str, str]] = []
        for raw in reader:
            # Ignore malformed overflow fields (DictReader stores them under key None).
            cleaned = {field: (raw.get(field, "") or "") for field in CSV_FIELDNAMES}
            rows.append(cleaned)
        return rows


def upsert_general_state(
    rows: List[Dict[str, str]],
    year: int,
    category_id: str,
    value: int,
    file_name: str,
    page: int,
    box: Optional[Tuple[float, float, float, float]],
) -> None:
    x, y, width, height = bbox_to_csv(box)
    new_row = {
        "year": str(year),
        "category_id": category_id,
        "value": str(value),
        "file": file_name,
        "page": str(page),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }

    replaced = False
    for i, existing in enumerate(rows):
        if existing.get("year", "") == str(year) and existing.get("category_id", "") == category_id:
            rows[i] = new_row
            replaced = True
            break

    if not replaced:
        rows.append(new_row)


def write_general_states(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDNAMES})
    tmp_path.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract attendance from stamma protocol PDF")
    parser.add_argument("meeting_year", type=int, help="Meeting year (e.g. 2025)")
    parser.add_argument("pdf_file", help="PDF filename under data/stamma_protocols/")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"Output CSV path (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Upsert attendance categories (6,7) into output CSV",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = PDF_DIR / args.pdf_file
    if not pdf_path.exists():
        print(f"Error: PDF not found: {pdf_path}", file=sys.stderr)
        return 1

    try:
        page, members, proxies, evidence, has_proxies, words, extraction_mode = extract_attendance(pdf_path)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    member_box, proxy_box = find_number_boxes(words, members, proxies, has_proxies)

    print(
        f"meeting_year={args.meeting_year} file={args.pdf_file} page={page} "
        f"members_present={members} proxy_count={proxies} extraction_mode={extraction_mode}"
    )
    print(f"evidence_text={evidence}")
    if member_box:
        x, y, w, h = bbox_to_csv(member_box)
        print(f"members_bbox={x},{y},{w},{h}")
    if proxy_box:
        x, y, w, h = bbox_to_csv(proxy_box)
        print(f"proxies_bbox={x},{y},{w},{h}")

    if args.append:
        out_path = Path(args.output)
        rows = load_existing_rows(out_path)
        upsert_general_state(rows, args.meeting_year, CATEGORY_MEMBERS, members, args.pdf_file, page, member_box)
        upsert_general_state(rows, args.meeting_year, CATEGORY_PROXIES, proxies, args.pdf_file, page, proxy_box)
        write_general_states(out_path, rows)
        print(f"Updated {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())