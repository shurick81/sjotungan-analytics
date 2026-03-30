#!/usr/bin/env python3
"""Extract board leadership rows into data/general_states.csv.

This script focuses on missing historical years (pre-2013) and writes
per-year extraction artifacts to extraction/artifacts/board_leadership/.

Upserted categories:
- 0: Rakenskapsar period (YYYY-01-01 – YYYY-12-31)
- 1: Ordförande
- 2: Vice ordförande
- 3: Ledamoter (semicolon-separated)
- 4: Valberedning (semicolon-separated)
"""

from __future__ import annotations

import argparse
import csv
import json
import html
import re
import subprocess
import tempfile
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple


CSV_FIELDNAMES = ["year", "category_id", "value", "file", "page", "x", "y", "width", "height"]
OUTPUT_CSV = Path("data/general_states.csv")
ANNUAL_REPORTS_DIR = Path("data/annual_reports")
ARTIFACTS_DIR = Path("extraction/artifacts/board_leadership")
WordBox = Tuple[float, float, float, float, str, str]


@dataclass(frozen=True)
class YearSource:
    year: int
    pdf: str


YEAR_SOURCES: Sequence[YearSource] = [
    YearSource(2003, "arsredovisning_03_B.pdf"),
    YearSource(2004, "Arsredovisning_2004.pdf"),
    YearSource(2005, "bokslut2005.pdf"),
    YearSource(2006, "Arsredovisning_2006.pdf"),
    YearSource(2007, "Arsredovisning_2007.pdf"),
    YearSource(2008, "Arsredovisning_2008.pdf"),
    YearSource(2009, "arsredovisning_2009.pdf"),
    YearSource(2010, "arsredovisning2010.pdf"),
    YearSource(2011, "arsredovisning2012.pdf"),
    YearSource(2012, "arsredovisning_2013.pdf"),
]


def normalize_text(text: str) -> str:
    lowered = text.lower().replace("\u00ad", "")
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_marks).strip()


def clean_name(text: str) -> str:
    text = text.strip(" .,:;|-\t")
    text = re.sub(r"\s+", " ", text)
    # Drop common leading role words that OCR can blend into name fields.
    text = re.sub(r"^(ordinarie|suppleant|styrelseledamot|ledamot)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(ordf\.?|ordforande|vice\s*ordf\.?|vice\s*ordforande).*$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,:;|-")


def is_probable_name(line: str) -> bool:
    n = normalize_text(line)
    if not n or any(ch.isdigit() for ch in line):
        return False

    blocked_tokens = [
        "styrelse",
        "ordinarie",
        "suppleant",
        "revisor",
        "valbered",
        "forening",
        "forvaltnings",
        "arsredovisning",
        "rakenskaps",
        "sida",
        "org",
        "datum",
        "stamma",
        "tur att avga",
        "sammankallande",
        "av hsb",
    ]
    if any(token in n for token in blocked_tokens):
        return False

    parts = [p for p in re.split(r"\s+", line.strip()) if p]
    if len(parts) < 2 or len(parts) > 6:
        return False

    alpha_tokens = [re.sub(r"[^A-Za-zÅÄÖåäöÉé\-]", "", p) for p in parts]
    alpha_tokens = [t for t in alpha_tokens if t]
    if len(alpha_tokens) < 2:
        return False

    uppercase_lead = sum(1 for token in alpha_tokens if token[0].isupper())
    return uppercase_lead >= 2


def extract_names_from_fragment(fragment: str) -> List[str]:
    cleaned_fragment = re.sub(r"(?i)\b(valberedning|har varit|sammankallande)\b", " ", fragment)
    cleaned_fragment = re.sub(r"\s+", " ", cleaned_fragment).strip(" .,:;|-\t")
    if not cleaned_fragment:
        return []

    names: List[str] = []
    for part in re.split(r"[;,]", cleaned_fragment):
        candidate = clean_name(part)
        if candidate and is_probable_name(candidate):
            names.append(candidate)
    return names


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
    for m in pattern.finditer(xml):
        x0, y0, x1, y1, raw = m.groups()
        decoded = html.unescape(raw).strip()
        if not decoded:
            continue
        words.append((float(x0), float(y0), float(x1), float(y1), decoded, normalize_token(decoded)))

    if words:
        return words

    # Fallback: OCR with TSV bounding boxes for scanned pages.
    with tempfile.TemporaryDirectory(prefix="board_bbox_ocr_") as tmpdir:
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
            return []

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
            return []

        px_to_pt = 72.0 / float(dpi)
        tsv = tsv_path.read_text(encoding="utf-8", errors="ignore")
        lines = tsv.splitlines()
        if not lines:
            return []

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
    xs0 = [b[0] for b in boxes]
    ys0 = [b[1] for b in boxes]
    xs1 = [b[2] for b in boxes]
    ys1 = [b[3] for b in boxes]
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def find_phrase_bbox(words: Sequence[WordBox], phrase: str) -> Optional[Tuple[float, float, float, float]]:
    tokens = [normalize_token(p) for p in phrase.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return None

    for i in range(len(words) - len(tokens) + 1):
        chunk = words[i : i + len(tokens)]
        if [w[5] for w in chunk] == tokens:
            return bbox_union([(w[0], w[1], w[2], w[3]) for w in chunk])

    # Fallback: all tokens exist somewhere on same line neighborhood.
    token_boxes = []
    for token in tokens:
        hit = next((w for w in words if w[5] == token), None)
        if not hit:
            return None
        token_boxes.append((hit[0], hit[1], hit[2], hit[3]))
    return bbox_union(token_boxes)


def bbox_to_csv(box: Optional[Tuple[float, float, float, float]]) -> Tuple[str, str, str, str]:
    if not box:
        return "", "", "", ""
    x0, y0, x1, y1 = box
    return (
        str(int(round(x0))),
        str(int(round(y0))),
        str(int(round(max(1.0, x1 - x0)))),
        str(int(round(max(1.0, y1 - y0)))),
    )


def get_page_count(pdf_path: Path) -> int:
    output = run_command(["pdfinfo", str(pdf_path)])
    match = re.search(r"^Pages:\s+(\d+)", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not read page count for {pdf_path.name}")
    return int(match.group(1))


def extract_page_text(pdf_path: Path, page: int) -> str:
    text = run_command(["pdftotext", "-f", str(page), "-l", str(page), str(pdf_path), "-"])
    if len(text.strip()) >= 40:
        return text

    # OCR fallback for scanned pages.
    with tempfile.TemporaryDirectory(prefix="board_ocr_") as tmpdir:
        image_prefix = Path(tmpdir) / "page"
        run_command([
            "pdftoppm",
            "-f",
            str(page),
            "-l",
            str(page),
            "-r",
            "250",
            "-png",
            str(pdf_path),
            str(image_prefix),
        ])
        image_files = sorted(Path(tmpdir).glob("page-*.png"))
        if not image_files:
            return text

        output_base = Path(tmpdir) / "ocr"
        run_command([
            "tesseract",
            str(image_files[0]),
            str(output_base),
            "-l",
            "swe+eng",
            "--psm",
            "6",
            "txt",
        ])
        txt_path = output_base.with_suffix(".txt")
        return txt_path.read_text(encoding="utf-8", errors="ignore") if txt_path.exists() else text


def page_score(text: str) -> int:
    n = normalize_text(text)
    score = 0
    for token in ["styrelse", "ordf", "vice", "ledamot", "valbered", "revisor"]:
        if token in n:
            score += 1
    if "styrelsen har sedan ordinarie" in n:
        score += 3
    return score


def choose_board_page(pdf_path: Path) -> Tuple[int, str]:
    page_count = get_page_count(pdf_path)
    best_page = 1
    best_text = ""
    best_score = -1

    # Board section is typically early in annual reports.
    for page in range(1, min(page_count, 12) + 1):
        text = extract_page_text(pdf_path, page)
        score = page_score(text)
        if score > best_score:
            best_score = score
            best_page = page
            best_text = text

    return best_page, best_text


def split_lines(text: str) -> List[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line]


def extract_roles(lines: Sequence[str]) -> Dict[str, object]:
    chair = ""
    vice = ""
    members: List[str] = []
    valberedning: List[str] = []
    ordinarie_names: List[str] = []

    # Pass 1: parse inline role rows such as "Bengt Rapp Ordförande".
    for line in lines:
        normalized = normalize_text(line)

        vice_match = re.search(r"^(.+?)\s+\b(v\.?\s*ordf[a-z]*\.?|vice\s*ordf[a-z]*\.?|vice\s*ordforande)\b", normalized)
        if vice_match:
            raw_name = line[: vice_match.start(2)]
            name = clean_name(raw_name)
            if name:
                vice = name
            continue

        chair_match = re.search(r"^(.+?)\s+\b(ordf[a-z]*\.?|ordforande)\b", normalized)
        if chair_match and "vice" not in normalized:
            raw_name = line[: chair_match.start(2)]
            name = clean_name(raw_name)
            if name:
                chair = name
            continue

        if " ledamot" in f" {normalized} ":
            raw_name = re.split(r"(?i)\bledamot\b", line, maxsplit=1)[0]
            name = clean_name(raw_name)
            if name and is_probable_name(name):
                members.append(name)

    # Pass 2: extract name list from "Ordinarie" section for older layouts.
    in_ordinarie = False
    for line in lines:
        normalized = normalize_text(line)

        if "ordinarie" in normalized and ("ledamot" in normalized or normalized == "ordinarie"):
            in_ordinarie = True
            continue

        if not in_ordinarie:
            continue

        if any(stop in normalized for stop in ["suppleant", "revisor", "valbered", "av hsb tillsatta", "tur att avga"]):
            break

        # Stop at role-only lines in legacy layout where role labels are listed separately.
        if re.fullmatch(r"(?i)(ordf\.?|v\.?ordf\.?|sek\.?|bygg|info|mark|webb|gym)", line.strip()):
            break

        candidate = re.split(r"(?i)\bledamot\b", line, maxsplit=1)[0]
        candidate = clean_name(candidate)
        if candidate and is_probable_name(candidate):
            ordinarie_names.append(candidate)

    # Fallback for heavily jumbled OCR layouts (for example 2005), where
    # ordinarie labels can appear before names in the same merged block.
    if not ordinarie_names:
        in_board_block = False
        for line in lines:
            normalized = normalize_text(line)
            if "styrelsen har sedan ordinarie" in normalized:
                in_board_block = True
                continue
            if not in_board_block:
                continue
            if any(stop in normalized for stop in ["revisor", "valbered"]):
                break
            candidate = re.split(r"(?i)\bledamot\b", line, maxsplit=1)[0]
            candidate = clean_name(candidate)
            if candidate and is_probable_name(candidate):
                ordinarie_names.append(candidate)

    # If chair/vice are still missing, infer from ordinarie ordering in legacy docs.
    if ordinarie_names and not chair:
        chair = ordinarie_names[0]
    if len(ordinarie_names) > 1 and not vice:
        vice = ordinarie_names[1]

    if ordinarie_names:
        for candidate in ordinarie_names:
            key = normalize_text(candidate)
            if key not in {normalize_text(chair), normalize_text(vice)}:
                members.append(candidate)

    # Deduplicate while preserving order.
    deduped_members: List[str] = []
    seen = set()
    for name in members:
        normalized_name = normalize_text(name)
        key = re.sub(r"\b(ledamot|sekreterare|och|info|utsedd|av|hsb|stockholm)\b", "", normalized_name)
        key = re.sub(r"\s+", " ", key).strip()
        if key in seen or not is_probable_name(name):
            continue
        seen.add(key)
        deduped_members.append(name)

    # Parse valberedning section.
    in_valberedning = False
    for line in lines:
        normalized = normalize_text(line)

        if "valberedning" in normalized and not in_valberedning:
            in_valberedning = True

            # Some years place the whole sentence on the same line as the
            # heading: "Valberedning har varit ...".
            trailing = re.split(r"(?i)\bvalberedning\b", line, maxsplit=1)
            tail = trailing[1] if len(trailing) > 1 else ""
            tail_normalized = normalize_text(tail)
            if "har varit" in tail_normalized:
                bits = re.split(r"(?i)\bhar varit\b", tail, maxsplit=1)
                fragment = bits[1] if len(bits) > 1 else tail
                valberedning.extend(extract_names_from_fragment(fragment))
            else:
                candidate = re.split(r"(?i)\bsammankallande\b", tail, maxsplit=1)[0]
                candidate = clean_name(candidate)
                if candidate and is_probable_name(candidate):
                    valberedning.append(candidate)
            continue

        if not in_valberedning:
            continue

        if any(
            stop in normalized
            for stop in [
                "foreningens stadgar",
                "studie",
                "fastighetsforvaltning",
                "representanter i hsb",
                "revisor",
            ]
        ):
            break

        if "har varit" in normalized:
            bits = re.split(r"(?i)\bhar varit\b", line, maxsplit=1)
            fragment = bits[1] if len(bits) > 1 else line
            valberedning.extend(extract_names_from_fragment(fragment))
            continue

        candidate = re.split(r"(?i)\bsammankallande\b", line, maxsplit=1)[0]
        candidate = clean_name(candidate)
        if candidate and is_probable_name(candidate):
            valberedning.append(candidate)

    deduped_valberedning: List[str] = []
    seen_valberedning = set()
    for name in valberedning:
        key = normalize_text(name)
        if key in seen_valberedning:
            continue
        seen_valberedning.add(key)
        deduped_valberedning.append(name)

    return {
        "chair": chair,
        "vice": vice,
        "members": deduped_members,
        "valberedning": deduped_valberedning,
    }


def load_existing_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: List[Dict[str, str]] = []
        for row in reader:
            rows.append({field: (row.get(field, "") or "") for field in CSV_FIELDNAMES})
        return rows


def upsert_row(
    rows: List[Dict[str, str]],
    year: int,
    category_id: int,
    value: str,
    pdf: str,
    page: int,
    box: Optional[Tuple[float, float, float, float]] = None,
) -> None:
    target_year = str(year)
    target_category = str(category_id)
    x, y, width, height = bbox_to_csv(box)
    new_row = {
        "year": target_year,
        "category_id": target_category,
        "value": value,
        "file": pdf,
        "page": str(page),
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }

    for idx, row in enumerate(rows):
        if row.get("year") == target_year and row.get("category_id") == target_category:
            rows[idx] = new_row
            return

    rows.append(new_row)


def write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDNAMES})


def save_artifacts(year: int, pdf: str, page: int, lines: Sequence[str], parsed: Dict[str, object]) -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    txt_path = ARTIFACTS_DIR / f"{year}_board_lines.txt"
    json_path = ARTIFACTS_DIR / f"{year}_board_extraction.json"

    txt_content = [f"year={year}", f"pdf={pdf}", f"page={page}", "", "lines:"]
    txt_content.extend(lines)
    txt_path.write_text("\n".join(txt_content) + "\n", encoding="utf-8")

    json_path.write_text(
        json.dumps(
            {
                "year": year,
                "pdf": pdf,
                "page": page,
                "parsed": parsed,
                "line_count": len(lines),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def extract_year(source: YearSource) -> Dict[str, object]:
    pdf_path = ANNUAL_REPORTS_DIR / source.pdf
    if not pdf_path.exists():
        raise FileNotFoundError(f"Missing source PDF: {pdf_path}")

    page, text = choose_board_page(pdf_path)
    lines = split_lines(text)
    parsed = extract_roles(lines)
    words = extract_page_words_bbox(pdf_path, page)

    chair = str(parsed.get("chair", "") or "")
    vice = str(parsed.get("vice", "") or "")
    members = parsed.get("members", []) if isinstance(parsed.get("members", []), list) else []
    valberedning = parsed.get("valberedning", []) if isinstance(parsed.get("valberedning", []), list) else []

    chair_box = find_phrase_bbox(words, chair) if chair else None
    vice_box = find_phrase_bbox(words, vice) if vice else None
    member_boxes = [find_phrase_bbox(words, name) for name in members]
    member_boxes = [b for b in member_boxes if b is not None]
    members_box = bbox_union(member_boxes)
    valberedning_boxes = [find_phrase_bbox(words, name) for name in valberedning]
    valberedning_boxes = [b for b in valberedning_boxes if b is not None]
    valberedning_box = bbox_union(valberedning_boxes)
    year_box = find_phrase_bbox(words, "Styrelse") or find_phrase_bbox(words, "Ordinarie")

    parsed["boxes"] = {
        "year": year_box,
        "chair": chair_box,
        "vice": vice_box,
        "members": members_box,
        "valberedning": valberedning_box,
    }
    save_artifacts(source.year, source.pdf, page, lines, parsed)

    return {
        "year": source.year,
        "pdf": source.pdf,
        "page": page,
        "chair": chair,
        "vice": vice,
        "members": members,
        "valberedning": valberedning,
        "boxes": parsed.get("boxes", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract board leadership rows")
    parser.add_argument("--append", action="store_true", help="Upsert extracted rows into data/general_states.csv")
    args = parser.parse_args()

    results: List[Dict[str, object]] = []
    for source in YEAR_SOURCES:
        print(f"[progress] extracting year {source.year} from {source.pdf}...", flush=True)
        result = extract_year(source)
        results.append(result)
        members = result["members"] if isinstance(result["members"], list) else []
        print(
            f"[progress] done {source.year}: chair={result['chair'] or '-'}, vice={result['vice'] or '-'}, members={len(members)}",
            flush=True,
        )

    for item in results:
        members = item["members"] if isinstance(item["members"], list) else []
        print(
            f"{item['year']},{item['pdf']},page={item['page']},"
            f"chair={item['chair'] or '-'},vice={item['vice'] or '-'},members={';'.join(members) or '-'}"
        )

    if args.append:
        rows = load_existing_rows(OUTPUT_CSV)
        for item in results:
            year = int(item["year"])
            page = int(item["page"])
            pdf = str(item["pdf"])
            chair = str(item["chair"] or "")
            vice = str(item["vice"] or "")
            members = item["members"] if isinstance(item["members"], list) else []
            valberedning = item["valberedning"] if isinstance(item.get("valberedning"), list) else []
            boxes = item["boxes"] if isinstance(item.get("boxes"), dict) else {}

            year_box = boxes.get("year")
            chair_box = boxes.get("chair")
            vice_box = boxes.get("vice")
            members_box = boxes.get("members")
            valberedning_box = boxes.get("valberedning")

            upsert_row(rows, year, 0, f"{year}-01-01 – {year}-12-31", pdf, page, year_box)
            upsert_row(rows, year, 1, chair, pdf, page, chair_box)
            upsert_row(rows, year, 2, vice, pdf, page, vice_box)
            upsert_row(rows, year, 3, ";".join(members), pdf, page, members_box)
            if valberedning:
                upsert_row(rows, year, 4, ";".join(valberedning), pdf, page, valberedning_box)

        write_rows(OUTPUT_CSV, rows)
        print(f"Upserted board rows into {OUTPUT_CSV}")

    print(f"Artifacts written under {ARTIFACTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
