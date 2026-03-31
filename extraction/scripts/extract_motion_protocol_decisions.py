#!/usr/bin/env python3
"""Extract stamma protocol decisions for motions and update data/motions.csv.

This script links each motion (year + motion_number) to protocol wording such as:
- "Beslut: Stamman beslutade att avsla motionen enligt styrelsens forslag till beslut."
- "Beslut: Stamman beslutade att bifalla motionen ..."
- "Beslut: ... anse motionen besvarad ..."

It writes/updates these columns in data/motions.csv:
- stamma_decision
- follows_styrelse_suggestion
- stamma_decision_wording
- stamma_followed_styrelse_binary
- stamma_protocol_file
- stamma_decision_page
- stamma_decision_x
- stamma_decision_y
- stamma_decision_width
- stamma_decision_height
- stamma_decision_evidence
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from extract_motion_resolutions import (
    HAS_OCR,
    extract_page_text_ocr,
    extract_page_words_bbox,
    extract_page_words_bbox_ocr,
    normalize_space,
    normalize_token,
)


DEFAULT_INPUT = Path("data/motions.csv")
PDF_DIR = Path("data/stamma_protocols")
YEAR_PATTERN = re.compile(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)")

CSV_FIELDS = [
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
    "stamma_decision",
    "follows_styrelse_suggestion",
    "stamma_decision_wording",
    "stamma_followed_styrelse_binary",
    "stamma_protocol_file",
    "stamma_decision_page",
    "stamma_decision_x",
    "stamma_decision_y",
    "stamma_decision_width",
    "stamma_decision_height",
    "stamma_decision_evidence",
]


@dataclass
class ProtocolDecision:
    motion_number: int
    decision: str
    follows: str
    page: int
    evidence: str
    x: str
    y: str
    width: str
    height: str


@dataclass
class ProtocolMotionMeta:
    motion_number: int
    title: str
    authors: str
    page: int


def run_command(command: List[str]) -> str:
    try:
        proc = subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError("Required command not found: pdfinfo/pdftotext") from exc
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.strip()
        raise RuntimeError(f"Command failed: {' '.join(command)}\\n{stderr}") from exc
    return proc.stdout


def get_pdf_page_count(pdf_path: Path) -> int:
    output = run_command(["pdfinfo", str(pdf_path)])
    match = re.search(r"^Pages:\s+(\d+)", output, flags=re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not determine page count for {pdf_path}")
    return int(match.group(1))


def extract_page_text(pdf_path: Path, page: int) -> str:
    return run_command([
        "pdftotext",
        "-f",
        str(page),
        "-l",
        str(page),
        str(pdf_path),
        "-",
    ])


def normalize_text(text: str) -> str:
    lowered = text.lower().replace("\u00ad", "")
    lowered = re.sub(r"(?<=\w)-\s+(?=\w)", "", lowered)
    decomposed = unicodedata.normalize("NFD", lowered)
    no_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", no_marks).strip()


def clean_evidence(text: str, limit: int = 220) -> str:
    compact = normalize_space(text)
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def clean_title(text: str) -> str:
    title = normalize_space(text).strip("\"'“”„”:- ")
    title = re.sub(r"\s+", " ", title)
    # "Ang." / "Angående" is usually a non-semantic heading prefix.
    title = re.sub(r"^ang(?:\.|(?:aende|ående)?)\s+", "", title, flags=re.IGNORECASE)
    # Common OCR slips in older protocols that hurt title quality.
    title = re.sub(r"\b[aä]nring\b", "ändring", title, flags=re.IGNORECASE)
    title = re.sub(r"\bgymet\b", "gymmet", title, flags=re.IGNORECASE)
    return title[:180].rstrip(" ,;:-")


def is_low_quality_protocol_title(title: str) -> bool:
    t = normalize_text(title)
    if not t:
        return True

    bad_markers = (
        "valberedningens forslag",
        "paragraferna",
        "styrelsens arsredovisning",
        "inklusive motion",
        "stamman avslutades",
        "justering",
        "stamman beslutade",
        "sekreterare",
        "stammoordforande",
    )
    return any(marker in t for marker in bad_markers)


def clean_authors(text: str) -> str:
    s = normalize_space(text)
    if not s:
        return ""

    # Remove common footer/noise fragments from scanned protocol pages.
    s = re.sub(r"\b(?:justering|sekreterare|st[aä]mmoordf[öo]rande|m[öo]tesordf[öo]randen)\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bM\s*\d{1,3}\b", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\bmyggdalsv[aä]gen\b.*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" ,;:-")

    # Keep likely personal names and separators.
    tokens = re.findall(r"[A-ZÅÄÖ][a-zåäöA-ZÅÄÖ-]+|och|,", s)
    if not tokens:
        return ""

    rebuilt = " ".join(tokens)
    rebuilt = re.sub(r"\s+,\s+", ", ", rebuilt)
    rebuilt = re.sub(r"\s+och\s+", " och ", rebuilt)
    rebuilt = re.sub(r"\s+", " ", rebuilt).strip(" ,")

    # Require at least one probable full name.
    if len(re.findall(r"[A-ZÅÄÖ][a-zåäöA-ZÅÄÖ-]+\s+[A-ZÅÄÖ][a-zåäöA-ZÅÄÖ-]+", rebuilt)) == 0:
        return ""

    return rebuilt[:180]


def split_inline_title_and_authors(text: str) -> Tuple[str, str]:
    c = clean_title(text)
    if not c:
        return "", ""

    # Strong pattern: title sentence followed by a person-name tail, often ending with a dot.
    name_tail = re.match(
        r"^(.*\.)\s+([A-ZÅÄÖ][A-Za-zÅÄÖåäö-]+(?:\s+[A-ZÅÄÖ][A-Za-zÅÄÖåäö-]+)+(?:\s*(?:,|och)\s*[A-ZÅÄÖ][A-Za-zÅÄÖåäö-]+(?:\s+[A-ZÅÄÖ][A-Za-zÅÄÖåäö-]+)*)*)\.?$",
        c,
    )
    if name_tail:
        title_part = clean_title(name_tail.group(1))
        trailing_authors = clean_authors(name_tail.group(2))
        if title_part and trailing_authors:
            return title_part, trailing_authors

    # Common protocol pattern: "Ang. ... . Firstname Lastname ..."
    # Use greedy title capture so abbreviations like "Ang." do not truncate the title.
    m = re.match(r"^(.*\.)\s+(.+)$", c)
    if not m:
        return c, ""

    title_part = clean_title(m.group(1))
    if normalize_text(title_part) in {"ang.", "ang"}:
        return c, ""

    trailing = m.group(2)
    trailing_low = normalize_text(trailing)
    if any(k in trailing_low for k in ("beslut", "stamman", "styrelsens svar", "styrelsens forslag")):
        return title_part, ""

    authors = clean_authors(trailing)
    return title_part, authors


def protocol_title_score(title: str) -> int:
    if not title:
        return -10_000

    score = min(len(title), 160)
    if title.lower().startswith("ang"):
        score += 60
    if not is_low_quality_protocol_title(title):
        score += 1000
    return score


def title_has_trailing_author_noise(title: str, authors: str) -> bool:
    if not title or not authors:
        return False
    title_norm = normalize_text(title).strip(" .,;:-")
    authors_norm = normalize_text(authors).strip(" .,;:-")
    return bool(authors_norm) and title_norm.endswith(authors_norm)


def title_has_appended_tail(existing_title: str, canonical_title: str) -> bool:
    if not existing_title or not canonical_title:
        return False
    existing_norm = normalize_text(existing_title).strip(" .,;:-")
    canonical_norm = normalize_text(canonical_title).strip(" .,;:-")
    if not existing_norm.startswith(canonical_norm):
        return False
    tail = existing_norm[len(canonical_norm) :].strip(" .,;:-")
    return len(tail) >= 8


def extract_title_from_section(lines: List[str], motion_number: int) -> str:
    if not lines:
        return ""

    header_re = re.compile(rf"\bmotion(?:en|erna)?\s*{motion_number}\b", flags=re.IGNORECASE)
    start_idx = 0
    for i, line in enumerate(lines):
        if header_re.search(line):
            start_idx = i + 1
            break

    skip_re = re.compile(
        r"^(?:"
        r"beslut\b|"
        r"styrelsens\s+svar\b|"
        r"styrelsens\s+forslag\b|"
        r"org\.nr\b|"
        r"brf\b|"
        r"myggdalsv[aä]gen\b|"
        r"sida\b|"
        r"\(motion\s*\d+\s+forts\.?\)|"
        r"motion\s*\d+\s+forts\.?"
        r")",
        flags=re.IGNORECASE,
    )
    author_hint_re = re.compile(r"\bmyggdalsv[aä]gen\b|\bM\d{1,3}\b", flags=re.IGNORECASE)

    candidates: List[str] = []
    authors_candidates: List[str] = []
    for raw in lines[start_idx : start_idx + 10]:
        if re.match(r"^\s*beslut\b", raw, flags=re.IGNORECASE):
            break
        if re.match(r"^\s*st[aä]mman\s+avslutades\b", raw, flags=re.IGNORECASE):
            break

        c = clean_title(raw)
        if not c:
            continue
        if skip_re.search(c):
            continue
        if len(re.sub(r"[^A-Za-zÅÄÖåäö]", "", c)) < 4:
            continue

        # If author/address starts on same line, keep only the leading title part.
        if author_hint_re.search(c):
            c = re.split(r"\b(?:Myggdalsv[aä]gen|M\d{1,3})\b", c, maxsplit=1, flags=re.IGNORECASE)[0]
            c = clean_title(c)
            if not c:
                continue

        # Remove a repeated heading prefix like "Motion angående".
        c = re.sub(r"^motion\s+(?:ang(?:aende|ående)?\.?\s+)?", "", c, flags=re.IGNORECASE)
        c, inline_authors = split_inline_title_and_authors(c)
        c = clean_title(c)
        if c:
            candidates.append(c)
        if inline_authors:
            authors_candidates.append(inline_authors)

        if not inline_authors:
            standalone_authors = clean_authors(raw)
            if standalone_authors:
                authors_candidates.append(standalone_authors)

        if len(candidates) >= 2:
            break

    if not candidates:
        return ""

    if len(candidates) == 1:
        return candidates[0]

    if candidates[0].endswith("."):
        return candidates[0]

    return clean_title(f"{candidates[0]} {candidates[1]}")


def extract_title_and_authors_from_section(lines: List[str], motion_number: int) -> Tuple[str, str]:
    if not lines:
        return "", ""

    header_re = re.compile(rf"\bmotion(?:en|erna)?\s*{motion_number}\b", flags=re.IGNORECASE)
    start_idx = 0
    for i, line in enumerate(lines):
        if header_re.search(line):
            start_idx = i + 1
            break

    title = ""
    authors = ""
    section_lines = lines[start_idx : start_idx + 10]
    for i, raw in enumerate(section_lines):
        if re.match(r"^\s*beslut\b", raw, flags=re.IGNORECASE):
            break
        if re.match(r"^\s*st[aä]mman\s+avslutades\b", raw, flags=re.IGNORECASE):
            break

        cleaned = clean_title(raw)
        if not cleaned:
            continue

        next_raw = section_lines[i + 1] if i + 1 < len(section_lines) else ""
        merged_cleaned = ""
        if next_raw and not re.match(r"^\s*beslut\b", next_raw, flags=re.IGNORECASE):
            merged_cleaned = clean_title(f"{raw} {next_raw}")

        # Prefer the existing title parsing behavior, but allow better split for inline author text.
        candidate_title, candidate_authors = split_inline_title_and_authors(cleaned)
        if merged_cleaned:
            merged_title, merged_authors = split_inline_title_and_authors(merged_cleaned)
            if merged_authors and len(merged_authors) > len(candidate_authors):
                candidate_title, candidate_authors = merged_title, merged_authors
        candidate_title = re.sub(r"^motion\s+(?:ang(?:aende|ående)?\.?\s+)?", "", candidate_title, flags=re.IGNORECASE)
        candidate_title = clean_title(candidate_title)

        if not title and candidate_title:
            title = candidate_title

        if candidate_authors and not authors:
            authors = candidate_authors

        if not authors:
            standalone_authors = clean_authors(raw)
            if merged_cleaned:
                merged_authors = clean_authors(f"{raw} {next_raw}")
                if len(merged_authors) > len(standalone_authors):
                    standalone_authors = merged_authors
            raw_norm = normalize_text(raw)
            if standalone_authors and not raw_norm.startswith("ang"):
                authors = standalone_authors

    # Fallback to previous title behavior when needed.
    if not title:
        title = extract_title_from_section(lines, motion_number)

    return clean_title(title), clean_authors(authors)


def detect_decision(normalized_block: str) -> Tuple[str, str]:
    patterns = [
        (r"(?:anse\s+motionen\s+besvarad|motionen\s+ar\s+besvarad)", "Besvarad"),
        (r"(?:att\s+)?besvar(?:a|ade|as)\s+motionen", "Besvarad"),
        (r"styrelsen\s+besvarade\s+motionen", "Besvarad"),
        (r"fr[aå]g(?:an|orna)\s+besvarad(?:e|es)", "Besvarad"),
        (r"(?:att\s+)?bifall(?:a|e[rs]?|s)\s+motionen", "Bifalls"),
        (r"(?:att\s+)?bifall\s+till\s+motionen", "Bifalls"),
        (r"motionen\s+bifall(?:e[rs]?|s)", "Bifalls"),
        (r"(?:att\s+)?godkann(?:a|er)\s+motionen", "Bifalls"),
        (r"(?:att\s+)?avsl[aå]\s+motionen", "Avslås"),
        (r"motionen\s+avsl[aå]s", "Avslås"),
        (r"avslag\s+(?:pa|på)\s+motionen", "Avslås"),
        (r"(?:aterremittera|aterremitterades|aterremitterad)\s+motionen", "Återremitterad"),
    ]

    for pattern, label in patterns:
        match = re.search(pattern, normalized_block)
        if match:
            return label, match.group(0)

    # Some protocols use free-form decision text inside a motion block
    # (for example: "Beslut: Tillägg till ...") without explicit bifall/avslag verbs.
    generic = re.search(r"beslut\s*:\s*([^\n]{6,180})", normalized_block)
    if generic:
        return "Beslutad", generic.group(1).strip()

    # Keep board-alignment text out of the wording column; this block still has a decision,
    # but without explicit bifall/avslag verbs.
    follows_board = re.search(r"enligt\s+styrelsens\s+(?:forslag|svar)(?:\s+till\s+beslut)?", normalized_block)
    if follows_board:
        return "Beslutad", follows_board.group(0)

    return "", ""


def detect_follow(normalized_block: str) -> str:
    if re.search(r"enligt\s+styrelsens\s+(?:forslag|svar)(?:\s+till\s+beslut)?", normalized_block):
        return "yes"
    if re.search(r"(?:mot|i\s+strid\s+med)\s+styrelsens\s+forslag", normalized_block):
        return "no"
    return "unknown"


def decision_wording_value(decision_label: str) -> str:
    # Keep wording only when the protocol states a concrete decision category.
    explicit_labels = {"Avslås", "Bifalls", "Besvarad", "Återremitterad"}
    return decision_label if decision_label in explicit_labels else ""


def infer_follow_from_resolution(board_resolution: str, stamma_decision: str) -> str:
    board = normalize_text(board_resolution)
    decision = normalize_text(stamma_decision)

    if board == "tillstyrker":
        return "yes" if decision == "bifalls" else "no" if decision == "avslas" else "unknown"
    if board == "avstyrker":
        return "yes" if decision == "avslas" else "no" if decision == "bifalls" else "unknown"

    return "unknown"


def follow_to_binary(follow: str) -> str:
    normalized = (follow or "").strip().lower()
    if normalized == "yes":
        return "1"
    if normalized == "no":
        return "0"
    return ""


def phrase_to_tokens(text: str) -> List[str]:
    return [normalize_token(tok) for tok in normalize_text(text).split() if normalize_token(tok)]


def _find_phrase_indices(
    words: List[Tuple[float, float, float, float, str, str]],
    phrase_tokens: List[str],
    max_skip: int = 10,
) -> Optional[List[int]]:
    if not phrase_tokens:
        return None

    normalized_words = [w[5] for w in words]
    first = phrase_tokens[0]

    for start in range(len(normalized_words)):
        if normalized_words[start] != first:
            continue

        matched = [start]
        pos = start + 1
        ok = True
        for token in phrase_tokens[1:]:
            found = False
            search_end = min(len(normalized_words), pos + max_skip + 1)
            for idx in range(pos, search_end):
                if normalized_words[idx] == token:
                    matched.append(idx)
                    pos = idx + 1
                    found = True
                    break
            if not found:
                ok = False
                break

        if ok:
            return matched

    return None


def _find_all_phrase_indices(
    words: List[Tuple[float, float, float, float, str, str]],
    phrase_tokens: List[str],
    max_skip: int = 10,
) -> List[List[int]]:
    if not phrase_tokens:
        return []

    matches: List[List[int]] = []
    normalized_words = [w[5] for w in words]
    first = phrase_tokens[0]

    for start in range(len(normalized_words)):
        if normalized_words[start] != first:
            continue

        matched = [start]
        pos = start + 1
        ok = True
        for token in phrase_tokens[1:]:
            found = False
            search_end = min(len(normalized_words), pos + max_skip + 1)
            for idx in range(pos, search_end):
                if normalized_words[idx] == token:
                    matched.append(idx)
                    pos = idx + 1
                    found = True
                    break
            if not found:
                ok = False
                break

        if ok:
            matches.append(matched)

    return matches


def _find_motion_anchor(
    words: List[Tuple[float, float, float, float, str, str]],
    motion_number: int,
) -> Optional[int]:
    indices = _find_phrase_indices(words, ["motion", str(motion_number)], max_skip=2)
    if indices:
        return indices[0]
    return None


def _select_motion_scoped_match(
    matches: List[List[int]],
    anchor_idx: Optional[int],
    next_anchor_idx: Optional[int],
) -> Optional[List[int]]:
    if not matches:
        return None

    if anchor_idx is None:
        return matches[0]

    scoped = []
    for match in matches:
        start = match[0]
        if start < anchor_idx:
            continue
        if next_anchor_idx is not None and start >= next_anchor_idx:
            continue
        scoped.append(match)

    if scoped:
        return scoped[0]

    return min(matches, key=lambda m: abs(m[0] - anchor_idx))


def _bbox_from_indices(
    words: List[Tuple[float, float, float, float, str, str]],
    indices: List[int],
) -> Tuple[str, str, str, str]:
    selected = [words[i] for i in indices]
    x0 = min(w[0] for w in selected)
    y0 = min(w[1] for w in selected)
    x1 = max(w[2] for w in selected)
    y1 = max(w[3] for w in selected)
    return (
        str(int(round(x0))),
        str(int(round(y0))),
        str(int(round(x1 - x0))),
        str(int(round(y1 - y0))),
    )


def find_stamma_decision_bbox(
    pdf_path: Path,
    page: int,
    motion_number: int,
    next_motion_number: Optional[int],
    decision: str,
    evidence: str,
    bbox_cache: Dict[int, List[Tuple[float, float, float, float, str, str]]],
    ocr_bbox_cache: Dict[int, List[Tuple[float, float, float, float, str, str]]],
) -> Tuple[str, str, str, str]:
    candidates: List[List[str]] = []

    evidence_tokens = phrase_to_tokens(evidence)
    if evidence_tokens:
        candidates.append(evidence_tokens)

    decision_token_variants = {
        "avslås": [["avslas"], ["avslag"]],
        "bifalls": [["bifall"], ["bifalls"], ["bifalles"]],
        "besvarad": [["besvarad"], ["besvarade"], ["besvarades"], ["besvaras"]],
        "enligt styrelsens förslag": [["enligt", "styrelsens", "forslag"]],
        "återremitterad": [["aterremitterad"], ["aterremitterades"], ["aterremittera"]],
    }.get(decision.lower().strip(), [])
    for tokens in decision_token_variants:
        candidates.append(tokens)

    words = bbox_cache.get(page)
    if words is None:
        words = extract_page_words_bbox(pdf_path, page)
        bbox_cache[page] = words

    anchor_idx = _find_motion_anchor(words, motion_number)
    next_anchor_idx = (
        _find_motion_anchor(words, next_motion_number) if next_motion_number is not None else None
    )

    for phrase_tokens in candidates:
        matches = _find_all_phrase_indices(words, phrase_tokens)
        idx = _select_motion_scoped_match(matches, anchor_idx, next_anchor_idx)
        if idx:
            return _bbox_from_indices(words, idx)

    if HAS_OCR:
        ocr_words = ocr_bbox_cache.get(page)
        if ocr_words is None:
            ocr_words = extract_page_words_bbox_ocr(pdf_path, page)
            ocr_bbox_cache[page] = ocr_words

        ocr_anchor_idx = _find_motion_anchor(ocr_words, motion_number)
        ocr_next_anchor_idx = (
            _find_motion_anchor(ocr_words, next_motion_number)
            if next_motion_number is not None
            else None
        )

        for phrase_tokens in candidates:
            matches = _find_all_phrase_indices(ocr_words, phrase_tokens)
            idx = _select_motion_scoped_match(matches, ocr_anchor_idx, ocr_next_anchor_idx)
            if idx:
                return _bbox_from_indices(ocr_words, idx)

    return "", "", "", ""


def parse_motion_sections(lines: List[str]) -> List[Tuple[int, int, int]]:
    header_pattern = re.compile(
        r"^\s*(?:inkomna\s+)?motion(?:en|erna)?\s*(?:nr\.?\s*)?([0-9il|å]{1,3})(?:\s*[-–]\s*([0-9il|å]{1,3}))?(?=\D|$)",
        flags=re.IGNORECASE,
    )

    def parse_ocr_number(token: str) -> Optional[int]:
        if not token:
            return None
        normalized = token.strip().lower()
        # Common OCR artifact in older scans: "Motion Å." should be interpreted as motion 1.
        if normalized == "å":
            normalized = "1"
        normalized = normalized.replace("|", "1").replace("l", "1").replace("i", "1")
        normalized = re.sub(r"\D", "", normalized)
        if not normalized:
            return None
        try:
            return int(normalized)
        except ValueError:
            return None

    header_blocks: List[Tuple[int, List[int]]] = []

    for idx, line in enumerate(lines):
        match = header_pattern.search(line)
        if not match:
            continue

        start_num = parse_ocr_number(match.group(1) or "")
        end_num = parse_ocr_number(match.group(2) or "")
        if start_num is None:
            continue

        numbers: List[int]
        if end_num is not None and end_num >= start_num and (end_num - start_num) <= 20:
            numbers = list(range(start_num, end_num + 1))
        else:
            numbers = [start_num]

        header_blocks.append((idx, numbers))

    def normalize_motion_number(number: int, prev_number: Optional[int], next_number: Optional[int]) -> int:
        if 1 <= number <= 99:
            return number

        token = str(number)
        candidates = set()
        if len(token) >= 2:
            candidates.add(int(token[:2]))
            candidates.add(int(token[-2:]))
        if len(token) >= 3:
            candidates.add(int(token[0] + token[-1]))

        candidates = {c for c in candidates if 1 <= c <= 99}
        if not candidates:
            return number

        def score(candidate: int) -> int:
            score_value = 0
            if prev_number is not None:
                if candidate < prev_number:
                    score_value += 100
                score_value += abs(candidate - (prev_number + 1))
            if next_number is not None:
                if candidate > next_number:
                    score_value += 100
                score_value += abs(next_number - candidate)
            return score_value

        return min(candidates, key=score)

    normalized_blocks: List[Tuple[int, List[int]]] = []
    prev_number: Optional[int] = None

    flat_headers: List[Tuple[int, int]] = []
    for start_idx, numbers in header_blocks:
        for number in numbers:
            flat_headers.append((start_idx, number))

    for i, (start_idx, number) in enumerate(flat_headers):
        next_number = None
        for _, nxt in flat_headers[i + 1 :]:
            if 1 <= nxt <= 99:
                next_number = nxt
                break
        normalized_number = normalize_motion_number(number, prev_number, next_number)
        prev_number = normalized_number

        if normalized_blocks and normalized_blocks[-1][0] == start_idx:
            normalized_blocks[-1][1].append(normalized_number)
        else:
            normalized_blocks.append((start_idx, [normalized_number]))

    sections: List[Tuple[int, int, int]] = []
    for i, (start_idx, numbers) in enumerate(normalized_blocks):
        end_idx = normalized_blocks[i + 1][0] if i + 1 < len(normalized_blocks) else len(lines)
        for number in numbers:
            sections.append((number, start_idx, end_idx))

    return sections


def extract_protocol_decisions(pdf_path: Path) -> Dict[int, ProtocolDecision]:
    page_count = get_pdf_page_count(pdf_path)
    decisions: Dict[int, ProtocolDecision] = {}
    bbox_cache: Dict[int, List[Tuple[float, float, float, float, str, str]]] = {}
    ocr_bbox_cache: Dict[int, List[Tuple[float, float, float, float, str, str]]] = {}

    for page in range(1, page_count + 1):
        page_text = extract_page_text(pdf_path, page)
        page_sources = [page_text]

        if HAS_OCR:
            ocr_text = extract_page_text_ocr(pdf_path, page)
            if ocr_text and normalize_text(ocr_text) != normalize_text(page_text):
                page_sources.append(ocr_text)

        for source_text in page_sources:
            lines = [line.strip() for line in source_text.splitlines() if line.strip()]
            if not lines:
                continue

            sections = parse_motion_sections(lines)
            if not sections:
                continue

            for section_index, (motion_number, start_idx, end_idx) in enumerate(sections):
                next_motion_number = None
                if section_index + 1 < len(sections):
                    next_motion_number = sections[section_index + 1][0]

                block = " ".join(lines[start_idx:end_idx])
                normalized_block = normalize_text(block)
                decision, decision_phrase = detect_decision(normalized_block)
                follow = detect_follow(normalized_block)

                if not decision and follow == "unknown":
                    continue

                evidence = decision_phrase if decision_phrase else clean_evidence(block)
                x, y, width, height = find_stamma_decision_bbox(
                    pdf_path,
                    page,
                    motion_number,
                    next_motion_number,
                    decision,
                    evidence,
                    bbox_cache,
                    ocr_bbox_cache,
                )
                candidate = ProtocolDecision(
                    motion_number=motion_number,
                    decision=decision,
                    follows=follow,
                    page=page,
                    evidence=evidence,
                    x=x,
                    y=y,
                    width=width,
                    height=height,
                )

                prev = decisions.get(motion_number)
                if prev is None:
                    decisions[motion_number] = candidate
                    continue

                # Prefer records with explicit decision labels over follow-only hits.
                if (not prev.decision) and candidate.decision:
                    decisions[motion_number] = candidate

    return decisions


def extract_protocol_motion_metadata(pdf_path: Path) -> Dict[int, ProtocolMotionMeta]:
    page_count = get_pdf_page_count(pdf_path)
    meta: Dict[int, ProtocolMotionMeta] = {}

    for page in range(1, page_count + 1):
        page_text = extract_page_text(pdf_path, page)
        page_sources = [page_text]

        if HAS_OCR:
            ocr_text = extract_page_text_ocr(pdf_path, page)
            if ocr_text and normalize_text(ocr_text) != normalize_text(page_text):
                page_sources.append(ocr_text)

        for source_text in page_sources:
            lines = [line.strip() for line in source_text.splitlines() if line.strip()]
            if not lines:
                continue

            sections = parse_motion_sections(lines)
            if not sections:
                continue

            for motion_number, start_idx, end_idx in sections:
                title, authors = extract_title_and_authors_from_section(lines[start_idx:end_idx], motion_number)
                if not title:
                    continue

                current = meta.get(motion_number)
                if current is None or protocol_title_score(title) > protocol_title_score(current.title):
                    meta[motion_number] = ProtocolMotionMeta(
                        motion_number=motion_number,
                        title=title,
                        authors=authors,
                        page=page,
                    )

    return meta


def extract_protocol_motion_numbers(pdf_path: Path) -> set[int]:
    page_count = get_pdf_page_count(pdf_path)
    motion_numbers: set[int] = set()

    for page in range(1, page_count + 1):
        page_text = extract_page_text(pdf_path, page)
        page_sources = [page_text]

        if HAS_OCR:
            ocr_text = extract_page_text_ocr(pdf_path, page)
            if ocr_text and normalize_text(ocr_text) != normalize_text(page_text):
                page_sources.append(ocr_text)

        for source_text in page_sources:
            lines = [line.strip() for line in source_text.splitlines() if line.strip()]
            if not lines:
                continue

            sections = parse_motion_sections(lines)
            for motion_number, _, _ in sections:
                if 1 <= motion_number <= 999:
                    motion_numbers.add(motion_number)

    return motion_numbers


def warn_duplicate_bboxes(year: int, protocol_file: str, decisions: Dict[int, ProtocolDecision]) -> None:
    by_bbox: Dict[Tuple[int, str, str, str, str], List[int]] = {}
    for motion_number, decision in decisions.items():
        if not all([decision.x, decision.y, decision.width, decision.height]):
            continue
        key = (decision.page, decision.x, decision.y, decision.width, decision.height)
        by_bbox.setdefault(key, []).append(motion_number)

    for (page, x, y, width, height), motion_numbers in sorted(by_bbox.items()):
        if len(motion_numbers) < 2:
            continue
        ordered = sorted(motion_numbers)
        print(
            f"WARNING: Duplicate stamma decision bbox in {protocol_file} ({year}) page {page}: motions {ordered} share x={x}, y={y}, w={width}, h={height}",
            file=sys.stderr,
        )


def ensure_fields(row: Dict[str, str]) -> Dict[str, str]:
    for field in CSV_FIELDS:
        row.setdefault(field, "")
    return row


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return [ensure_fields(dict(r)) for r in reader]


def select_protocol_file_for_year(year: int) -> Optional[str]:
    candidates = []
    token = str(year)

    for path in sorted(PDF_DIR.glob("*.pdf")):
        name = path.name
        if token not in name:
            continue

        normalized = name.lower()
        score = 0
        if "protokoll" in normalized:
            score += 4
        if "stamma" in normalized:
            score += 2
        if "extra" in normalized:
            score -= 5

        candidates.append((score, -len(name), name))

    if not candidates:
        return None

    candidates.sort(reverse=True)
    return candidates[0][2]


def infer_year_from_protocol_filename(filename: str) -> Optional[int]:
    years = [int(y) for y in YEAR_PATTERN.findall(filename)]
    if not years:
        return None

    # Most files include exactly one relevant year token.
    for year in years:
        if 1990 <= year <= 2100:
            return year
    return years[0]


def update_rows_for_year(
    rows: List[Dict[str, str]],
    year: int,
    protocol_file: str,
    decisions: Dict[int, ProtocolDecision],
    motion_meta: Dict[int, ProtocolMotionMeta],
) -> int:
    updated = 0

    for row in rows:
        if row.get("year", "").strip() != str(year):
            continue

        raw_motion = row.get("motion_number", "").strip()
        if not raw_motion.isdigit():
            continue

        motion_number = int(raw_motion)
        decision = decisions.get(motion_number)
        meta = motion_meta.get(motion_number)
        existing_title = row.get("title", "").strip()
        existing_authors = row.get("authors", "").strip()
        protocol_derived_title = (row.get("file", "").strip() == protocol_file)
        should_replace_title = bool(
            meta
            and existing_title
            and protocol_derived_title
            and (
                is_low_quality_protocol_title(existing_title)
                or title_has_trailing_author_noise(existing_title, existing_authors)
                or title_has_appended_tail(existing_title, meta.title)
            )
            and not is_low_quality_protocol_title(meta.title)
        )
        if decision is None:
            # If annual-report extraction missed title/file/page, use protocol motion metadata.
            if meta and (not existing_title or should_replace_title):
                row["title"] = meta.title
                row["file"] = protocol_file
                row["page"] = str(meta.page)
            if meta and not (row.get("authors", "").strip()) and meta.authors:
                row["authors"] = meta.authors
                updated += 1
            elif meta and (not existing_title or should_replace_title):
                updated += 1
            continue

        if meta and (not existing_title or should_replace_title):
            row["title"] = meta.title
            row["file"] = protocol_file
            row["page"] = str(meta.page)
        if meta and not (row.get("authors", "").strip()) and meta.authors:
            row["authors"] = meta.authors

        row["stamma_decision"] = ""
        row["stamma_decision_wording"] = decision_wording_value(decision.decision)
        row["stamma_protocol_file"] = protocol_file
        row["stamma_decision_page"] = str(decision.page)
        row["stamma_decision_x"] = decision.x
        row["stamma_decision_y"] = decision.y
        row["stamma_decision_width"] = decision.width
        row["stamma_decision_height"] = decision.height
        row["stamma_decision_evidence"] = decision.evidence

        follow = decision.follows
        if follow == "unknown" and decision.decision:
            follow = infer_follow_from_resolution(row.get("resolution", ""), decision.decision)
        row["follows_styrelse_suggestion"] = ""
        row["stamma_followed_styrelse_binary"] = follow_to_binary(follow)
        updated += 1

    return updated


def append_missing_rows_for_year(
    rows: List[Dict[str, str]],
    year: int,
    protocol_file: str,
    decisions: Dict[int, ProtocolDecision],
    protocol_motion_numbers: Optional[set[int]] = None,
) -> int:
    existing = {
        int(r["motion_number"])
        for r in rows
        if r.get("year", "").strip() == str(year) and r.get("motion_number", "").strip().isdigit()
    }

    created = 0
    all_motion_numbers = set(decisions)
    if protocol_motion_numbers:
        all_motion_numbers.update(protocol_motion_numbers)

    for motion_number in sorted(all_motion_numbers):
        if motion_number in existing:
            continue

        decision = decisions.get(motion_number)
        if decision:
            follow = decision.follows
            if follow == "unknown":
                follow = infer_follow_from_resolution("", decision.decision)
        else:
            follow = "unknown"

        new_row = {field: "" for field in CSV_FIELDS}
        new_row["year"] = str(year)
        new_row["motion_number"] = str(motion_number)
        if decision:
            new_row["stamma_decision"] = ""
            new_row["stamma_decision_wording"] = decision_wording_value(decision.decision)
            new_row["stamma_decision_page"] = str(decision.page)
            new_row["stamma_decision_x"] = decision.x
            new_row["stamma_decision_y"] = decision.y
            new_row["stamma_decision_width"] = decision.width
            new_row["stamma_decision_height"] = decision.height
            new_row["stamma_decision_evidence"] = decision.evidence
        else:
            new_row["stamma_decision_evidence"] = "Motion header found in protocol; decision text not confidently parsed."
        new_row["follows_styrelse_suggestion"] = ""
        new_row["stamma_followed_styrelse_binary"] = follow_to_binary(follow)
        new_row["stamma_protocol_file"] = protocol_file
        rows.append(new_row)
        created += 1

    return created


def write_rows(path: Path, rows: List[Dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("year", type=int, nargs="?", help="Meeting year in motions.csv")
    parser.add_argument("pdf_file", nargs="?", help="Protocol PDF filename in data/stamma_protocols")
    parser.add_argument(
        "--all-years",
        action="store_true",
        help="Process all years found in data/motions.csv (auto-select protocol file by year)",
    )
    parser.add_argument(
        "--all-protocols",
        action="store_true",
        help="Process all protocol PDFs (infer year from filename) and allow protocol-first backfill",
    )
    parser.add_argument(
        "--bootstrap-missing",
        action="store_true",
        help="Create missing (year, motion_number) rows from protocol decisions when absent in motions.csv",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Update data/motions.csv in-place",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Path to motions CSV (default: data/motions.csv)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not args.append:
        print("Use --append to write updates into data/motions.csv", file=sys.stderr)
        return 2

    if not args.input.exists():
        print(f"Motions CSV not found: {args.input}", file=sys.stderr)
        return 2

    rows = load_rows(args.input)
    years_in_csv = sorted({int(r["year"]) for r in rows if r.get("year", "").isdigit()})

    plans: List[Tuple[int, str]] = []
    if args.all_years and args.all_protocols:
        print("Use either --all-years or --all-protocols, not both", file=sys.stderr)
        return 2

    if args.all_protocols:
        for pdf_path in sorted(PDF_DIR.glob("*.pdf")):
            year = infer_year_from_protocol_filename(pdf_path.name)
            if year is None:
                print(f"WARNING: Could not infer year from protocol filename: {pdf_path.name}", file=sys.stderr)
                continue
            plans.append((year, pdf_path.name))
    elif args.all_years:
        for year in years_in_csv:
            inferred = select_protocol_file_for_year(year)
            if inferred:
                plans.append((year, inferred))
            else:
                print(f"WARNING: No protocol PDF found for year {year}", file=sys.stderr)
    else:
        if args.year is None or args.pdf_file is None:
            print("Provide YEAR and PDF_FILE, or use --all-years", file=sys.stderr)
            return 2
        plans.append((args.year, args.pdf_file))

    if not plans:
        print("No years queued for extraction.", file=sys.stderr)
        return 1

    total_updated = 0
    total_created = 0
    for year, pdf_file in plans:
        pdf_path = PDF_DIR / pdf_file
        if not pdf_path.exists():
            print(f"WARNING: Protocol file not found for {year}: {pdf_file}", file=sys.stderr)
            continue

        decisions = extract_protocol_decisions(pdf_path)
        motion_meta = extract_protocol_motion_metadata(pdf_path)
        warn_duplicate_bboxes(year, pdf_file, decisions)
        updated = update_rows_for_year(rows, year, pdf_file, decisions, motion_meta)
        created = 0
        if args.bootstrap_missing:
            protocol_motion_numbers = extract_protocol_motion_numbers(pdf_path)
            created = append_missing_rows_for_year(
                rows,
                year,
                pdf_file,
                decisions,
                protocol_motion_numbers,
            )
        total_updated += updated
        total_created += created
        print(
            f"{year}: detected {len(decisions)} protocol motion decision(s), updated {updated} motion row(s), created {created} missing row(s).",
            file=sys.stderr,
        )

    rows.sort(
        key=lambda r: (
            int(r["year"]) if r.get("year", "").isdigit() else 0,
            int(r["motion_number"]) if r.get("motion_number", "").isdigit() else 0,
            r.get("file", ""),
        )
    )
    write_rows(args.input, rows)
    print(f"Updated rows total: {total_updated}, created rows total: {total_created} in {args.input}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
