#!/usr/bin/env python3
"""Extract board leadership rows into data/general_states.csv.

This script extracts annual-report board roles and writes per-year artifacts to
extraction/artifacts/board_leadership/.

Upserted categories:
- 0: Rakenskapsar period (YYYY-01-01 – YYYY-12-31)
- 1: Ordförande
- 2: Vice ordförande
- 3: Ledamoter (semicolon-separated)
- 4: Valberedning (semicolon-separated)
- 5: Revisorer (semicolon-separated)
- 8: Suppleanter (semicolon-separated)
- 9: Revisorer signerat årsredovisningen (semicolon-separated)
"""

from __future__ import annotations

import argparse
import csv
from difflib import SequenceMatcher
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
    YearSource(2012, "arsredovisning2013.pdf"),
    YearSource(2013, "arsredovisning_2013.pdf"),
    YearSource(2014, "arsredovisning2014.pdf"),
    YearSource(2015, "arsredovisning2015.pdf"),
    YearSource(2016, "arsredovisning_2016.pdf"),
    YearSource(2017, "kallelse_stamma2018.pdf"),
    YearSource(2018, "kallelse_stamma_2019.pdf"),
    YearSource(2019, "BRF_Sjotungan_arsredovisning_2019.pdf"),
    YearSource(2020, "stamma_kallelse-2021.pdf"),
    YearSource(2021, "stamma_kallelse_2022.pdf"),
    YearSource(2022, "stamma-2023.pdf"),
    YearSource(2023, "stamma-2024.pdf"),
    YearSource(2024, "stamma2025.pdf"),
]


def normalize_text(text: str) -> str:
    lowered = text.lower().replace("\u00ad", "")
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_marks).strip()


def clean_name(text: str) -> str:
    text = text.strip(" .,:;|-\t")
    # OCR often injects digits into names (for example "Gé6ran").
    text = re.sub(r"\d+", "", text)
    text = re.sub(r"\s+", " ", text)
    # Drop common leading role words that OCR can blend into name fields.
    text = re.sub(r"^(ordinarie|suppleant|styrelseledamot|ledamot)\s+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+(ordf\.?|ordforande|vice\s*ordf\.?|vice\s*ordforande).*$", "", text, flags=re.IGNORECASE)
    return text.strip(" .,:;|-")


def is_probable_name(line: str) -> bool:
    digit_count = sum(1 for ch in line if ch.isdigit())
    if digit_count > 2:
        return False

    line_clean = re.sub(r"\d+", "", line)
    n = normalize_text(line_clean)
    if not n:
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

    parts = [p for p in re.split(r"\s+", line_clean.strip()) if p]
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


def dedupe_preserve(values: Sequence[str]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for value in values:
        key = normalize_text(value)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(value)
    return deduped


def normalize_firm_name(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" .,:;|-\t")
    cleaned = re.sub(r"(?i)\bav\s+hsb\b.*$", "", cleaned).strip(" .,:;|-\t")
    cleaned = re.sub(r"(?i)\bab\b\.?$", "", cleaned).strip(" .,:;|-\t")
    cleaned = re.sub(r"(?i)\bbo\s+revision\b", "BoRevision", cleaned)
    return cleaned


def extract_revisor_signers(lines: Sequence[str]) -> List[str]:
    if not lines:
        return []

    start_idx: Optional[int] = None
    for idx, line in enumerate(lines):
        if "digitalt signerad av" in normalize_text(line):
            start_idx = idx + 1
            break

    # Digital-sign pages place names right after the marker, while scanned
    # reports usually put signatures near the bottom of the page.
    if start_idx is None:
        start_idx = max(0, len(lines) - 35)

    window = list(lines[start_idx : start_idx + 40])
    if not window:
        window = list(lines)

    signers: List[str] = []

    def is_strict_person_name(text: str) -> bool:
        candidate = clean_name(text)
        if not candidate:
            return False

        normalized = normalize_text(candidate)
        blocked = [
            "signerat",
            "digitalt",
            "uttalanden",
            "grund for uttalanden",
            "stockholm",
            "tyreso",
            "revisionsberattelse",
            "arsredovisning",
            "foreningen",
            "penneo",
            "dokumentnyckel",
            "ordforande",
            "styrelse",
            "hsb",
            "riksforbund",
            "borevision",
            "international",
            "standards",
            "auditing",
            "enligt",
            "revisorns",
            "revisors",
            "sverige",
        ]
        if any(token in normalized for token in blocked):
            return False

        if re.search(r"\d", candidate):
            return False

        if not re.fullmatch(r"[A-Za-zÅÄÖåäöÉé\-\s]+", candidate):
            return False

        parts = [p for p in candidate.split() if p]
        if len(parts) < 2 or len(parts) > 4:
            return False

        if any(len(part) < 2 for part in parts):
            return False

        # Reject OCR noise such as "Bar Dar" where every token is implausibly short.
        if max(len(part) for part in parts) < 4:
            return False

        # Reject OCR fragments like "Zbz" that contain no vowels.
        if any(not re.search(r"[aeiouyåäöéAEIOUYÅÄÖÉ]", part) for part in parts):
            return False

        return is_probable_name(candidate)

    for line in window:
        normalized = normalize_text(line)
        if not normalized:
            continue

        if any(stop in normalized for stop in ["penneo", "dokumentnyckel", "page", "sida"]):
            break

        if any(token in normalized for token in ["borevision", "bo revision", "revision ab", "kungsbron"]):
            continue

        # Strip role/firm suffixes that OCR often appends to the same line.
        line_for_names = re.split(
            r"(?i)\b(?:borevision|bo\s*revision|av\s+foreningen\s+vald\s+revisor|av\s+hsb\s+riksforbund\s+utsedd\s+revisor|utsedd\s+revisor|vald\s+revisor)\b",
            line,
            maxsplit=1,
        )[0]

        for fragment in re.split(r"[;,]", line_for_names):
            candidate = clean_name(fragment)
            if not candidate:
                continue

            parts = [p for p in candidate.split() if p]
            # OCR can merge two names on one line: "Joakim Hall Olle Wicander".
            if len(parts) == 4:
                left = f"{parts[0]} {parts[1]}"
                right = f"{parts[2]} {parts[3]}"
                if is_strict_person_name(left):
                    signers.append(left)
                if is_strict_person_name(right):
                    signers.append(right)
                continue

            if is_strict_person_name(candidate):
                signers.append(candidate)

            # Fallback for OCR lines where names are embedded in a long sentence,
            # for example "... Joakim Hall Olle Wicander ...".
            if any(ctx in normalized for ctx in ["stockholm", "revisor", "foreningen", "riksforbund"]):
                word_tokens = re.findall(r"[A-Za-zÅÄÖåäöÉé\-]+", candidate)
                capitalized = [token for token in word_tokens if token and token[0].isupper()]
                if len(capitalized) >= 4:
                    pair_candidates = [
                        f"{capitalized[-4]} {capitalized[-3]}",
                        f"{capitalized[-2]} {capitalized[-1]}",
                    ]
                    for pair in pair_candidates:
                        if is_strict_person_name(pair):
                            signers.append(pair)

    return dedupe_preserve(signers)


def extract_board_revisors(lines: Sequence[str]) -> List[str]:
    revisors: List[str] = []
    in_revisor_section = False

    for line in lines:
        normalized = normalize_text(line)
        if not normalized:
            continue

        if "revisor" in normalized and not in_revisor_section:
            in_revisor_section = True

        if not in_revisor_section:
            continue

        if any(
            stop in normalized
            for stop in [
                "representanter i hsb",
                "representant pa hsb",
                "valberedning",
                "studie och fritidsverksamhet",
                "fastighetsforvaltning",
                "foreningens stadgar",
            ]
        ):
            break

        candidate_line = line
        candidate_line = re.split(r"(?i)\brevisor har varit\b", candidate_line, maxsplit=1)[-1]
        candidate_line = re.split(r"(?i)\bsamt en revisor\b", candidate_line, maxsplit=1)[0]
        candidate_line = re.split(r"(?i)\bmed\b", candidate_line, maxsplit=1)[0]
        candidate_line = re.split(r"(?i)\bvalda vid\b", candidate_line, maxsplit=1)[0]
        candidate_line = re.split(r"(?i)\butsedd av\b", candidate_line, maxsplit=1)[0]
        candidate_line = re.split(r"(?i)\bhos\s+bo\s*revision\b", candidate_line, maxsplit=1)[0]
        candidate_line = re.sub(r"(?i)^\s*revisor\s+", "", candidate_line).strip()

        if not candidate_line:
            continue

        candidate_line = re.sub(r"(?i)\boch\b", ";", candidate_line)
        for fragment in re.split(r"[;,:]", candidate_line):
            candidate = clean_name(fragment)
            if candidate and is_probable_name(candidate):
                revisors.append(candidate)

    return dedupe_preserve(revisors)


def _names_look_related(left: str, right: str) -> bool:
    ln = normalize_text(left)
    rn = normalize_text(right)
    if not ln or not rn:
        return False
    if ln == rn:
        return True

    l_tokens = [t for t in re.split(r"\s+", ln) if t]
    r_tokens = [t for t in re.split(r"\s+", rn) if t]
    if not l_tokens or not r_tokens:
        return False

    if l_tokens[-1] == r_tokens[-1]:
        return True

    return SequenceMatcher(None, ln, rn).ratio() >= 0.74


def reconcile_signed_revisors(
    signed_revisors: Sequence[str],
    board_revisors: Sequence[str],
) -> List[str]:
    signed = dedupe_preserve(list(signed_revisors))
    board = dedupe_preserve(list(board_revisors))
    if not board:
        return signed
    if not signed:
        return board

    if any(_names_look_related(signed_name, board_name) for signed_name in signed for board_name in board):
        return signed

    # If signer-page OCR names do not resemble board-revisor names at all,
    # trust board-page names for older scanned reports.
    return board


def run_command(command: List[str]) -> str:
    proc = subprocess.run(command, check=True, capture_output=True, text=True)
    return proc.stdout


def normalize_token(text: str) -> str:
    lowered = text.strip().lower()
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    token = re.sub(r"[^0-9a-z]+", "", no_marks)
    # OCR in scanned pages can substitute similar-looking characters.
    return (
        token.replace("0", "o")
        .replace("1", "l")
        .replace("3", "e")
        .replace("5", "s")
        .replace("6", "o")
        .replace("8", "b")
    )


def tokens_match(expected: str, actual: str) -> bool:
    if expected == actual:
        return True
    if not expected or not actual:
        return False
    if expected[0] != actual[0]:
        return False

    ratio = SequenceMatcher(None, expected, actual).ratio()
    if ratio >= 0.82:
        return True

    # Accept a single edit for short OCR glitches in otherwise matching names.
    if abs(len(expected) - len(actual)) <= 1 and ratio >= 0.72:
        return True
    return False


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
        if all(tokens_match(expected, actual) for expected, actual in zip(tokens, [w[5] for w in chunk])):
            return bbox_union([(w[0], w[1], w[2], w[3]) for w in chunk])

    # Fallback: all tokens exist somewhere on same line neighborhood.
    token_boxes = []
    for token in tokens:
        hit = next((w for w in words if tokens_match(token, w[5])), None)
        if not hit:
            return None
        token_boxes.append((hit[0], hit[1], hit[2], hit[3]))
    return bbox_union(token_boxes)


def find_lower_page_region_bbox(words: Sequence[WordBox], start_ratio: float = 0.55) -> Optional[Tuple[float, float, float, float]]:
    if not words:
        return None

    top = min(word[1] for word in words)
    bottom = max(word[3] for word in words)
    threshold = top + (bottom - top) * start_ratio
    region_boxes = [(word[0], word[1], word[2], word[3]) for word in words if word[1] >= threshold]
    if len(region_boxes) < 3:
        return None
    return bbox_union(region_boxes)


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


def choose_signed_revisor_page(pdf_path: Path) -> Tuple[Optional[int], str]:
    page_count = get_page_count(pdf_path)
    best_page: Optional[int] = None
    best_text = ""
    best_score = -1

    start_page = max(1, page_count - 30)
    for page in range(start_page, page_count + 1):
        text = extract_page_text(pdf_path, page)
        n = normalize_text(text)
        score = 0
        if "rapport om arsredovisningen" in n:
            score += 6
        if "rapport om andra krav" in n:
            score += 5
        if "digitalt signerad av" in n:
            score += 6
        if "av foreningen vald revisor" in n:
            score += 3
        if "utsedd revisor" in n:
            score += 2
        if "stockholm den" in n:
            score += 2
        if "stockholm" in n:
            score += 1
        if score > best_score or (score == best_score and best_page is not None and page > best_page):
            best_score = score
            best_page = page
            best_text = text

    if best_score < 6:
        return None, ""
    return best_page, best_text


def split_lines(text: str) -> List[str]:
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return [line for line in lines if line]


def extract_roles(lines: Sequence[str]) -> Dict[str, object]:
    chair = ""
    vice = ""
    members: List[str] = []
    valberedning: List[str] = []
    suppleanter: List[str] = []
    ordinarie_names: List[str] = []
    legacy_inference = any("styrelsen har sedan ordinarie" in normalize_text(line) for line in lines)

    # Pass 1: parse inline role rows such as "Bengt Rapp Ordförande".
    for line in lines:
        normalized = normalize_text(line)

        vice_match = re.search(r"^(.+?)\s+\b(v\.?\s*ordf[a-z]*\.?|vice\s*ordf[a-z]*\.?|vice\s*ordforande)\b", normalized)
        if vice_match:
            raw_name = line[: vice_match.start(2)]
            name = clean_name(raw_name)
            if name and is_probable_name(name):
                vice = name
            continue

        chair_match = re.search(r"^(.+?)\s+\b(ordf[a-z]*\.?|ordforande)\b", normalized)
        if chair_match and "vice" not in normalized:
            raw_name = line[: chair_match.start(2)]
            name = clean_name(raw_name)
            if name and is_probable_name(name):
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
    if legacy_inference and ordinarie_names and not chair:
        chair = ordinarie_names[0]
    if legacy_inference and len(ordinarie_names) > 1 and not vice:
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

    # Parse suppleanter section.
    in_suppleanter = False
    for line in lines:
        normalized = normalize_text(line)

        if "suppleanter" in normalized and not in_suppleanter:
            in_suppleanter = True
            trailing = re.split(r"(?i)\bsuppleanter\b", line, maxsplit=1)
            tail = trailing[1] if len(trailing) > 1 else ""
            candidate = re.split(r"(?i)\bsuppleant(er)?\b", tail, maxsplit=1)[0]
            candidate = clean_name(candidate)
            if candidate and is_probable_name(candidate):
                suppleanter.append(candidate)
            continue

        if not in_suppleanter:
            continue

        if any(
            stop in normalized
            for stop in [
                "i tur att avga",
                "tur att avga",
                "revisor",
                "valbered",
                "representanter i hsb",
                "foreningens stadgar",
                "studie",
            ]
        ):
            break

        candidate = re.split(r"(?i)\bsuppleant(er)?\b", line, maxsplit=1)[0]
        candidate = clean_name(candidate)
        if candidate and is_probable_name(candidate):
            suppleanter.append(candidate)

    deduped_suppleanter: List[str] = []
    seen_suppleanter = set()
    for name in suppleanter:
        key = normalize_text(name)
        if key in seen_suppleanter:
            continue
        seen_suppleanter.add(key)
        deduped_suppleanter.append(name)

    return {
        "chair": chair,
        "vice": vice,
        "members": deduped_members,
        "valberedning": deduped_valberedning,
        "suppleanter": deduped_suppleanter,
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


def _row_brief(row: Dict[str, str]) -> str:
    return (
        f"year={row.get('year', '')!r},cat={row.get('category_id', '')!r},"
        f"file={row.get('file', '')!r},page={row.get('page', '')!r},value={row.get('value', '')[:60]!r}"
    )


def _count_blank_year_rows(rows: Sequence[Dict[str, str]]) -> int:
    return sum(1 for row in rows if not (row.get("year") or "").strip())


def assert_no_existing_year_loss(before_rows: Sequence[Dict[str, str]], after_rows: Sequence[Dict[str, str]]) -> None:
    lost: List[str] = []
    for idx, before in enumerate(before_rows):
        before_year = (before.get("year") or "").strip()
        if not before_year:
            continue

        if idx >= len(after_rows):
            lost.append(f"idx={idx}: missing row after update ({_row_brief(before)})")
            continue

        after_year = (after_rows[idx].get("year") or "").strip()
        if not after_year:
            lost.append(
                f"idx={idx}: year lost ({_row_brief(before)}) -> ({_row_brief(after_rows[idx])})"
            )

        if len(lost) >= 10:
            break

    if lost:
        raise RuntimeError(
            "Refusing to write CSV because at least one existing row lost its year value. "
            f"Examples: {lost}"
        )


def assert_no_new_blank_year_rows(before_rows: Sequence[Dict[str, str]], after_rows: Sequence[Dict[str, str]]) -> None:
    before_blank = _count_blank_year_rows(before_rows)
    after_blank = _count_blank_year_rows(after_rows)
    if after_blank <= before_blank:
        return

    new_blanks = [row for row in after_rows if not (row.get("year") or "").strip()]
    sample = [_row_brief(row) for row in new_blanks[:10]]
    raise RuntimeError(
        "Refusing to write CSV because append/update introduced additional blank-year rows. "
        f"before_blank={before_blank}, after_blank={after_blank}, sample={sample}"
    )


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
    board_revisors = extract_board_revisors(lines)
    words = extract_page_words_bbox(pdf_path, page)

    signed_page, signed_text = choose_signed_revisor_page(pdf_path)
    signed_lines = split_lines(signed_text) if signed_text else []
    signed_revisors = extract_revisor_signers(signed_lines)
    signed_words = extract_page_words_bbox(pdf_path, signed_page) if signed_page else []

    # Older scanned annual reports can place signer names on the following page
    # after the "Rapport om arsredovisningen" section.
    if signed_page and not signed_revisors:
        page_count = get_page_count(pdf_path)
        if signed_page < page_count:
            next_page = signed_page + 1
            next_text = extract_page_text(pdf_path, next_page)
            next_lines = split_lines(next_text)
            next_revisors = extract_revisor_signers(next_lines)
            if next_revisors:
                signed_page = next_page
                signed_text = next_text
                signed_lines = next_lines
                signed_revisors = next_revisors
                signed_words = extract_page_words_bbox(pdf_path, signed_page)
    chair = str(parsed.get("chair", "") or "")
    vice = str(parsed.get("vice", "") or "")
    members = parsed.get("members", []) if isinstance(parsed.get("members", []), list) else []
    valberedning = parsed.get("valberedning", []) if isinstance(parsed.get("valberedning", []), list) else []
    suppleanter = parsed.get("suppleanter", []) if isinstance(parsed.get("suppleanter", []), list) else []
    signed_revisor_boxes = [find_phrase_bbox(signed_words, entry) for entry in signed_revisors]
    signed_revisor_boxes = [b for b in signed_revisor_boxes if b is not None]
    signed_revisor_box = bbox_union(signed_revisor_boxes)

    if signed_revisor_box is None and signed_words:
        anchor_boxes = [
            find_phrase_bbox(signed_words, "Stockholm"),
            find_phrase_bbox(signed_words, "Av föreningen vald revisor"),
            find_phrase_bbox(signed_words, "Av HSB Riksförbund utsedd revisor"),
        ]
        anchor_boxes = [b for b in anchor_boxes if b is not None]
        signed_revisor_box = bbox_union(anchor_boxes)

    if not signed_revisors and signed_page and board_revisors:
        signed_revisors = board_revisors
        if signed_revisor_box is None:
            signed_revisor_box = find_lower_page_region_bbox(signed_words) or None

    signed_revisors = reconcile_signed_revisors(signed_revisors, board_revisors)

    chair_box = find_phrase_bbox(words, chair) if chair else None
    vice_box = find_phrase_bbox(words, vice) if vice else None
    member_boxes = [find_phrase_bbox(words, name) for name in members]
    member_boxes = [b for b in member_boxes if b is not None]
    members_box = bbox_union(member_boxes)
    valberedning_boxes = [find_phrase_bbox(words, name) for name in valberedning]
    valberedning_boxes = [b for b in valberedning_boxes if b is not None]
    valberedning_box = bbox_union(valberedning_boxes)
    suppleant_boxes = [find_phrase_bbox(words, name) for name in suppleanter]
    suppleant_boxes = [b for b in suppleant_boxes if b is not None]
    suppleanter_box = bbox_union(suppleant_boxes)
    board_revisor_boxes = [find_phrase_bbox(words, name) for name in board_revisors]
    board_revisor_boxes = [b for b in board_revisor_boxes if b is not None]
    board_revisor_box = bbox_union(board_revisor_boxes)
    year_box = find_phrase_bbox(words, "Styrelse") or find_phrase_bbox(words, "Ordinarie")

    if signed_revisor_box is None and signed_revisors:
        signed_revisor_box = board_revisor_box

    parsed["boxes"] = {
        "year": year_box,
        "chair": chair_box,
        "vice": vice_box,
        "members": members_box,
        "valberedning": valberedning_box,
        "suppleanter": suppleanter_box,
        "revisorer_signed": signed_revisor_box,
    }
    parsed["board_revisorer"] = board_revisors
    parsed["revisorer_signed"] = signed_revisors
    parsed["revisorer_signed_page"] = signed_page
    save_artifacts(source.year, source.pdf, page, lines, parsed)

    return {
        "year": source.year,
        "pdf": source.pdf,
        "page": page,
        "chair": chair,
        "vice": vice,
        "members": members,
        "valberedning": valberedning,
        "suppleanter": suppleanter,
        "revisorer_signed": signed_revisors,
        "revisorer_signed_page": signed_page,
        "boxes": parsed.get("boxes", {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract board leadership rows")
    parser.add_argument("--append", action="store_true", help="Upsert extracted rows into data/general_states.csv")
    parser.add_argument(
        "--years",
        nargs="+",
        type=int,
        help="Optional year filter, for example: --years 2015 2016",
    )
    args = parser.parse_args()

    selected_sources = list(YEAR_SOURCES)
    if args.years:
        requested_years = set(args.years)
        selected_sources = [source for source in YEAR_SOURCES if source.year in requested_years]
        missing = sorted(requested_years - {source.year for source in selected_sources})
        if missing:
            raise SystemExit(f"Unsupported year(s): {', '.join(str(y) for y in missing)}")

    results: List[Dict[str, object]] = []
    for source in selected_sources:
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
        rows_before_append = [dict(row) for row in rows]
        for item in results:
            year = int(item["year"])
            page = int(item["page"])
            pdf = str(item["pdf"])
            chair = str(item["chair"] or "")
            vice = str(item["vice"] or "")
            members = item["members"] if isinstance(item["members"], list) else []
            valberedning = item["valberedning"] if isinstance(item.get("valberedning"), list) else []
            suppleanter = item["suppleanter"] if isinstance(item.get("suppleanter"), list) else []
            revisorer_signed = item["revisorer_signed"] if isinstance(item.get("revisorer_signed"), list) else []
            revisorer_signed_page = item.get("revisorer_signed_page")
            boxes = item["boxes"] if isinstance(item.get("boxes"), dict) else {}

            year_box = boxes.get("year")
            chair_box = boxes.get("chair")
            vice_box = boxes.get("vice")
            members_box = boxes.get("members")
            valberedning_box = boxes.get("valberedning")
            suppleanter_box = boxes.get("suppleanter")
            revisorer_signed_box = boxes.get("revisorer_signed")
            has_board_roles = bool(chair or vice or members or valberedning or suppleanter)
            existing_signed_row = next(
                (
                    row
                    for row in rows_before_append
                    if row.get("year") == str(year) and row.get("category_id") == "9"
                ),
                None,
            )

            if has_board_roles and year_box is not None:
                upsert_row(rows, year, 0, f"{year}-01-01 – {year}-12-31", pdf, page, year_box)
            if chair:
                upsert_row(rows, year, 1, chair, pdf, page, chair_box)
            if vice:
                upsert_row(rows, year, 2, vice, pdf, page, vice_box)
            if members:
                upsert_row(rows, year, 3, ";".join(members), pdf, page, members_box)
            if valberedning:
                upsert_row(rows, year, 4, ";".join(valberedning), pdf, page, valberedning_box)
            if isinstance(revisorer_signed_page, int) and not revisorer_signed:
                raise RuntimeError(
                    "Refusing to write stale category 9 data: "
                    f"year={year} found signer page={revisorer_signed_page} but parsed zero signers. "
                    f"existing_row={_row_brief(existing_signed_row) if existing_signed_row else 'None'}"
                )
            if revisorer_signed and isinstance(revisorer_signed_page, int):
                upsert_row(
                    rows,
                    year,
                    9,
                    ";".join(dedupe_preserve(revisorer_signed)),
                    pdf,
                    int(revisorer_signed_page),
                    revisorer_signed_box,
                )
            if suppleanter:
                upsert_row(rows, year, 8, ";".join(suppleanter), pdf, page, suppleanter_box)

        assert_no_existing_year_loss(rows_before_append, rows)
        assert_no_new_blank_year_rows(rows_before_append, rows)

        write_rows(OUTPUT_CSV, rows)
        print(f"Upserted board rows into {OUTPUT_CSV}")

    print(f"Artifacts written under {ARTIFACTS_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
