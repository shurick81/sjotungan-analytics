#!/usr/bin/env python3
"""Extract motion resolution outcomes from annual meeting PDF files.

This script targets sections like "Styrelsens yttrande" and maps
recommendation wording to a normalized resolution value:
  - Tillstyrker
  - Avstyrker
  - Delvis tillstyrker
  - Oklar

Output CSV format matches data/motions.csv:
year,file,motion_number,page,title,authors,resolution,resolution_page,resolution_x,resolution_y,resolution_width,resolution_height
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from pdf2image import convert_from_path
    import pytesseract

    HAS_OCR = True
except Exception:
    HAS_OCR = False


DEFAULT_OUTPUT = Path("data/motions.csv")
PDF_DIR = Path("data/annual_reports")


@dataclass
class MotionContext:
    number: Optional[int]
    title: str
    page: int


@dataclass
class ResolutionRow:
    year: int
    file: str
    motion_number: str
    page: int
    title: str
    authors: str
    resolution: str
    resolution_page: int
    resolution_x: str
    resolution_y: str
    resolution_width: str
    resolution_height: str


def run_command(command: List[str]) -> str:
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Required command not found: pdftotext") from exc
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


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def find_motion_context(
    pages: Dict[int, str],
    resolution_page: int,
    match_pos_on_page: Optional[int] = None,
) -> MotionContext:
    motion_pattern = re.compile(r"\b(?:MOTION|Motion)\s*(\d+)\b")

    # Prefer nearest MOTION heading on the same page before the matched phrase.
    if match_pos_on_page is not None:
        compact_page = normalize_space(pages.get(resolution_page, ""))
        same_page_matches = list(motion_pattern.finditer(compact_page))
        prior_matches = [m for m in same_page_matches if m.start() <= match_pos_on_page]
        if prior_matches:
            number = int(prior_matches[-1].group(1))
            return MotionContext(number=number, title=f"Motion {number}", page=resolution_page)

    for page in range(resolution_page, 0, -1):
        text = pages.get(page, "")
        page_matches = list(motion_pattern.finditer(text))
        if page_matches:
            number = int(page_matches[-1].group(1))
            return MotionContext(number=number, title=f"Motion {number}", page=page)
    return MotionContext(number=None, title="Motion (okänd)", page=resolution_page)


def _clean_line(line: str) -> str:
    return normalize_space(line).strip(" :-")


def _extract_authors_from_lines(lines: List[str]) -> str:
    # Typical signer row format: "Namn Efternamn, Myggdalsvägen 82"
    signer_pattern = re.compile(r"^[A-Za-zÅÄÖåäöÉé\-\s\.]+,\s*[^,]+\d+.*$")
    name_only_pattern = re.compile(r"^[A-Za-zÅÄÖåäöÉé\-\s\.]{3,80}$")
    address_hint_pattern = re.compile(
        r"(myggdals|v[aä]gen|v\.|gatan|gr[aä]nd|all[eé]|torg)",
        flags=re.IGNORECASE,
    )

    def is_likely_name(candidate: str) -> bool:
        if not name_only_pattern.match(candidate):
            return False
        low = candidate.lower().strip()
        if low.startswith("brf ") or low.startswith("org.nr") or low.startswith("motion"):
            return False
        if low.startswith("styrelsen") or low.startswith("yrkar"):
            return False
        # Exclude location/date rows and sentence fragments by enforcing person-name shape.
        tokens = [t for t in re.split(r"\s+", candidate) if t]
        if len(tokens) < 2 or len(tokens) > 4:
            return False

        token_pattern = re.compile(r"^([A-ZÅÄÖ]\.|[A-Za-zÅÄÖåäöÉé][A-Za-zÅÄÖåäöÉé\-]*\.?)$")
        return all(token_pattern.match(t) for t in tokens)

    authors: List[str] = []
    for idx, line in enumerate(lines):
        c = _clean_line(line)
        if not c:
            continue
        if signer_pattern.match(c):
            name_part = c.split(",", 1)[0].strip()
            if is_likely_name(name_part):
                authors.append(name_part)
            continue

        # Also support signatures written as a name line followed by an address line.
        if is_likely_name(c):
            next_line = ""
            if idx + 1 < len(lines):
                next_line = _clean_line(lines[idx + 1])
            if next_line and address_hint_pattern.search(next_line):
                authors.append(c)

    # De-duplicate while preserving order
    seen = set()
    uniq = []
    for a in authors:
        key = a.lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(a)
    return ";".join(uniq)


def _slice_motion_block(lines: List[str], motion_number: Optional[int]) -> List[str]:
    if motion_number is None:
        return lines

    motion_header = re.compile(r"\bmotion\s*(\d+)\b", flags=re.IGNORECASE)
    header_positions: List[Tuple[int, int]] = []
    for i, line in enumerate(lines):
        m = motion_header.search(line)
        if not m:
            continue
        try:
            num = int(m.group(1))
        except ValueError:
            continue
        header_positions.append((i, num))

    if not header_positions:
        return lines

    start_idx = None
    for idx, num in header_positions:
        if num == motion_number:
            start_idx = idx
            break

    if start_idx is None:
        return lines

    end_idx = len(lines)
    for idx, _num in header_positions:
        if idx > start_idx:
            end_idx = idx
            break

    return lines[start_idx:end_idx]


def _extract_title_from_lines(lines: List[str], motion_number: Optional[int]) -> str:
    def summarize_title(raw_title: str) -> str:
        title = normalize_space(raw_title).strip('"“”')

        # Remove common boilerplate heading before the actual subject.
        title = re.sub(
            r"^till\s+brf\s+sj[oö]tungan\s+styrelsen,?\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"^motion\s+till\s+.*?årsstämma\s+\d{1,2}\s+[a-zåäö]+\s+\d{4}\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"^motion\s+till\s+styrelsen\s+.*?\d{4}-\d{2}-\d{2}\s*",
            "",
            title,
            flags=re.IGNORECASE,
        )
        title = re.sub(
            r"^jag\s+skulle\s+vilja\s+l[aä]mna\s+in\s+en\s+motion\s+f[oö]r\s+att\s+diskutera\s+",
            "",
            title,
            flags=re.IGNORECASE,
        )

        # Keep concise subject from "Motion ang ..." headings.
        m_ang = re.match(
            r"^motion\s+ang\s+(.+?)(?:\s+(?:det|jag|vi|som)\b|$)",
            title,
            flags=re.IGNORECASE,
        )
        if m_ang:
            title = m_ang.group(1).strip(" ,;:-")

        # Also support variants like "Ang." and "Motion angående ...".
        m_ang_var = re.match(
            r"^(?:motion\s+)?ang(?:[aå]ende)?\.?\s+(.+?)(?:\s+(?:det|jag|vi|som)\b|$)",
            title,
            flags=re.IGNORECASE,
        )
        if m_ang_var:
            title = m_ang_var.group(1).strip(" ,;:-")

        # Drop leading list markers like "1." before clause splitting.
        title = re.sub(r"^\d+\.\s*", "", title)

        title = normalize_space(title).strip('"“”')

        # Narrative openings are often the first sentence of the full motion text,
        # not a true heading. Convert common narrative patterns into concise topics.
        low_title = title.lower()
        narrative_starts = (
            "det har ",
            "jag skulle ",
            "vi som ",
            "i över ",
        )
        if low_title.startswith(narrative_starts):
            if "cykel" in low_title:
                if "ställ" in low_title or "förvara" in low_title or "platser" in low_title:
                    title = "Fler cykelplatser i området"
                else:
                    title = "Cykelparkering i området"
            elif ("husbil" in low_title or "husvagn" in low_title) and "parkering" in low_title:
                title = "Parkeringsplatser för husbilar och husvagnar"
            elif "uteplats" in low_title and ("hyres" in low_title or "arrende" in low_title):
                title = "Ökning av hyreskostnad för uteplats"
            elif "el" in low_title and ("hyra" in low_title or "avgift" in low_title):
                title = "El i månadsavgiften"
            elif "rök" in low_title:
                title = "Rökfri bostadsrättsförening"

        # Topic-based fallback even if salutation was removed before narrative check.
        low_title = title.lower()
        if "uteplats" in low_title and ("hyres" in low_title or "arrende" in low_title):
            title = "Ökning av hyreskostnad för uteplats"
        elif "denna motion gäller området" in low_title and ("träd" in low_title or "fruktträd" in low_title):
            title = "Trädfällning och fruktträd mellan port 56-58 och 66-68"
        elif ("husbil" in low_title or "husvagn" in low_title) and "parkering" in low_title:
            title = "Parkeringsplatser för husbilar och husvagnar"
        elif "bredband" in low_title and ("vattenbesparing" in low_title or "varmvatten" in low_title):
            title = "Bredband och vattenbesparing"
        elif "tappvarmvatten" in low_title and ("individuell" in low_title or "mätning" in low_title):
            title = "Individuell mätning av tappvarmvatten"
        elif "sortering av matavfall" in low_title and (
            "handlingsplan" in low_title
            or "utredning" in low_title
            or "utreda" in low_title
            or "vid årsmöte" in low_title
        ):
            title = "Handlingsplan för sortering av matavfall"
        elif "individuell reglering av värme" in low_title or (
            "element" in low_title and "värme" in low_title
        ):
            title = "Individuell reglering av värme"

        # Prefer first clause as display title.
        first_clause = re.split(r"[\.!?]", title, maxsplit=1)[0].strip()
        if first_clause:
            title = first_clause

        # Hard cap for readability in CSV viewers.
        max_words = 14
        words = title.split()
        if len(words) > max_words:
            title = " ".join(words[:max_words]).rstrip(" ,;:-") + "..."

        return title or (f"Motion {motion_number}" if motion_number is not None else "Motion (okänd)")

    start_idx = 0
    if motion_number is not None:
        motion_header = re.compile(rf"\bmotion\s*{motion_number}\b", flags=re.IGNORECASE)
    else:
        motion_header = re.compile(r"\bmotion\s*\d+\b", flags=re.IGNORECASE)
    for i, line in enumerate(lines):
        if motion_header.search(line):
            start_idx = i + 1
            break

    candidates: List[str] = []
    for raw in lines[start_idx:]:
        c = _clean_line(raw)
        if not c:
            continue
        low = c.lower()
        if low.startswith("brf sjötungan") or low.startswith("myggdalsvägen") or low.startswith("org.nr"):
            continue
        if low.startswith("vi som författat"):
            break
        # Skip obvious signer rows
        if "," in c and re.search(r"\d", c):
            continue
        candidates.append(c)
        if len(candidates) >= 2:
            break

    if candidates:
        if len(candidates) == 1:
            return summarize_title(candidates[0])
        return summarize_title(f"{candidates[0]} {candidates[1]}")

    if motion_number is not None:
        return f"Motion {motion_number}"
    return "Motion (okänd)"


def extract_motion_metadata_ocr(pdf_path: Path, page: int, motion_number: Optional[int]) -> Tuple[str, str]:
    if not HAS_OCR:
        if motion_number is not None:
            return f"Motion {motion_number}", ""
        return "Motion (okänd)", ""

    try:
        image = convert_from_path(str(pdf_path), dpi=300, first_page=page, last_page=page)[0]
        text = pytesseract.image_to_string(image, lang="swe")
    except Exception:
        if motion_number is not None:
            return f"Motion {motion_number}", ""
        return "Motion (okänd)", ""

    lines = [ln for ln in (l.strip() for l in text.splitlines()) if ln]
    motion_lines = _slice_motion_block(lines, motion_number)
    title = _extract_title_from_lines(motion_lines, motion_number)
    authors = _extract_authors_from_lines(motion_lines)
    return title, authors


def detect_resolutions(text: str) -> List[Tuple[str, str, int]]:
    compact = normalize_space(text).lower()

    yrkar_styrelsen = r"(?:styrelsen\s+yrkar|yrkar(?:\s+\w+){0,3}\s+styrelsen)"
    foreslar_styrelsen = r"(?:styrelsen\s+f[oö]resl[aå]r|f[oö]resl[aå]r\s+styrelsen)"

    patterns = [
        (rf"{yrkar_styrelsen}\s+.*?delvis\s+bifall", "Delvis tillstyrker"),
        (rf"{yrkar_styrelsen}\s+.*?delvis\s+avslag", "Delvis tillstyrker"),
        (rf"{yrkar_styrelsen}\s+.*?\bbifall\b", "Tillstyrker"),
        # Keep explicit "motionen avslas" as outcome label Avslås instead of Avstyrker.
        (rf"{yrkar_styrelsen}\s+.*?\bavslag\b", "Avstyrker"),
        (rf"{foreslar_styrelsen}\s+.*?\bbifall\b", "Tillstyrker"),
        (rf"{foreslar_styrelsen}\s+.*?\bavslag\b", "Avstyrker"),
        # Keep passive outcome wording when stated explicitly in the source text.
        (r"motionen\s+bifalles", "Bifalls"),
        (r"motionen\s+avsl[aå]s", "Avslås"),
        (r"motionen\s+[aä]r\s+besvarad", "Besvarad"),
    ]

    found: List[Tuple[int, int, str]] = []
    for pattern, resolution in patterns:
        for m in re.finditer(pattern, compact):
            found.append((m.start(), m.end(), resolution))

    if not found:
        return []

    found.sort(key=lambda x: x[0])
    results: List[Tuple[str, str, int]] = []
    last_kept_end = -1
    for start_idx, end_idx, resolution in found:
        # Skip near-duplicate overlapping matches from different patterns.
        if start_idx < last_kept_end:
            continue
        start = max(0, start_idx - 90)
        end = min(len(compact), end_idx + 90)
        snippet = compact[start:end].strip()
        results.append((resolution, snippet, start_idx))
        last_kept_end = end_idx

    return results


def build_rows(year: int, pdf_file: str) -> List[ResolutionRow]:
    pdf_path = PDF_DIR / pdf_file
    if not pdf_path.exists():
        raise RuntimeError(f"PDF file not found: {pdf_path}")

    page_count = get_pdf_page_count(pdf_path)
    pages: Dict[int, str] = {}
    rows: List[ResolutionRow] = []
    seen_motion_keys = set()

    for page in range(1, page_count + 1):
        text = extract_page_text(pdf_path, page)
        pages[page] = text
        matches = detect_resolutions(text)
        if not matches:
            continue

        for resolution, _snippet, match_pos in matches:
            ctx = find_motion_context(pages, page, match_pos_on_page=match_pos)
            motion_key = (ctx.number, resolution, page)
            if motion_key in seen_motion_keys:
                continue
            seen_motion_keys.add(motion_key)

            title, authors = extract_motion_metadata_ocr(pdf_path, ctx.page, ctx.number)

            rows.append(
                ResolutionRow(
                    year=year,
                    file=pdf_file,
                    motion_number=str(ctx.number) if ctx.number is not None else "",
                    page=ctx.page,
                    title=title,
                    authors=authors,
                    resolution=resolution,
                    resolution_page=page,
                    resolution_x="",
                    resolution_y="",
                    resolution_width="",
                    resolution_height="",
                )
            )

    return rows


def print_rows(rows: List[ResolutionRow]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "year",
            "file",
            "motion_number",
            "page",
            "title",
            "authors",
            "resolution",
            "resolution_page",
            "resolution_x",
            "resolution_y",
            "resolution_width",
            "resolution_height",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.year,
                row.file,
                row.motion_number,
                row.page,
                row.title,
                row.authors,
                row.resolution,
                row.resolution_page,
                row.resolution_x,
                row.resolution_y,
                row.resolution_width,
                row.resolution_height,
            ]
        )


def append_rows(output_path: Path, rows: List[ResolutionRow]) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "year",
        "file",
        "motion_number",
        "page",
        "title",
        "authors",
        "resolution",
        "resolution_page",
        "resolution_x",
        "resolution_y",
        "resolution_width",
        "resolution_height",
    ]

    def infer_motion_number(d: Dict[str, str]) -> str:
        raw = normalize_space(d.get("motion_number", ""))
        if raw:
            return raw
        title = d.get("title", "")
        m = re.search(r"\bmotion\s*(\d+)\b", title, flags=re.IGNORECASE)
        if m:
            return m.group(1)
        return ""

    def make_key(d: Dict[str, str]) -> Tuple[str, str, str]:
        motion_number = infer_motion_number(d)
        if motion_number:
            return (d["year"], d["file"], motion_number)
        # Fallback for rows where motion number cannot be determined.
        return (d["year"], d["file"], f"p{d['page']}_r{d['resolution_page']}")

    def make_legacy_key(d: Dict[str, str]) -> Tuple[str, str, str, str]:
        return (d["year"], d["file"], d["page"], d["resolution_page"])

    existing: List[Dict[str, str]] = []
    if output_path.exists():
        with output_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                existing.append(r)

    # Remove duplicate records already present in the CSV, keeping last occurrence.
    compressed: Dict[Tuple[str, str, str], Dict[str, str]] = {}
    order: List[Tuple[str, str, str]] = []
    for r in existing:
        key = make_key(r)
        if key not in compressed:
            order.append(key)
        compressed[key] = r
    existing = [compressed[k] for k in order]

    # Secondary dedupe for legacy collisions that differ only by missing motion_number.
    by_legacy: Dict[Tuple[str, str, str, str], Dict[str, str]] = {}
    legacy_order: List[Tuple[str, str, str, str]] = []
    for r in existing:
        lk = make_legacy_key(r)
        if lk not in by_legacy:
            by_legacy[lk] = r
            legacy_order.append(lk)
            continue
        prev = by_legacy[lk]
        prev_has_num = bool(infer_motion_number(prev))
        curr_has_num = bool(infer_motion_number(r))
        if curr_has_num and not prev_has_num:
            by_legacy[lk] = r
        else:
            by_legacy[lk] = r
    existing = [by_legacy[k] for k in legacy_order]

    existing_idx = {make_key(r): i for i, r in enumerate(existing)}
    legacy_idx = {make_legacy_key(r): i for i, r in enumerate(existing)}
    added = 0

    for row in rows:
        record = {
            "year": str(row.year),
            "file": row.file,
            "motion_number": row.motion_number,
            "page": str(row.page),
            "title": row.title,
            "authors": row.authors,
            "resolution": row.resolution,
            "resolution_page": str(row.resolution_page),
            "resolution_x": row.resolution_x,
            "resolution_y": row.resolution_y,
            "resolution_width": row.resolution_width,
            "resolution_height": row.resolution_height,
        }
        key = make_key(record)
        if key in existing_idx:
            existing[existing_idx[key]] = record
        else:
            legacy_key = make_legacy_key(record)
            if legacy_key in legacy_idx and not record["motion_number"]:
                idx = legacy_idx[legacy_key]
                existing[idx] = record
                existing_idx[key] = idx
            else:
                existing.append(record)
                idx = len(existing) - 1
                existing_idx[key] = idx
                legacy_idx[legacy_key] = idx
                added += 1

    with output_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in existing:
            writer.writerow(r)

    return added


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("year", type=int, help="Motion/meeting year (e.g. 2025 for stamma2025.pdf)")
    parser.add_argument("pdf_file", help="PDF filename in data/annual_reports")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append extracted rows to data/motions.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output CSV path for --append mode",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = build_rows(args.year, args.pdf_file)

    if not rows:
        print("No motion resolution rows were detected.", file=sys.stderr)
        return 1

    if args.append:
        added = append_rows(args.output, rows)
        print(f"Detected {len(rows)} row(s), appended {added} new row(s) to {args.output}.")
        return 0

    print_rows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
