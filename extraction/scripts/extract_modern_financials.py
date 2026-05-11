#!/usr/bin/env python3
"""Extract financial_states and financial_events rows from a modern annual report PDF.

Target format: stamma2026.pdf and later annual reports where the Resultaträkning,
Balansräkning, Kassaflödesanalys and Noter use the standard HSB layout with a
"2025-01-01–2025-12-31  /  2024-01-01–2024-12-31" two-column structure.

The script matches labels by regex against the per-page layout text, picks the
current-year (left) column value, then computes a bbox by locating the value
token in the page's word stream (`pdftotext -bbox-layout`).
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


PDF_DIR = Path("data/annual_reports")
STATES_CSV = Path("data/financial_states.csv")
EVENTS_CSV = Path("data/financial_events.csv")

STATES_FIELDS = ["year", "category_id", "amount", "file", "page", "x", "y", "width", "height"]
EVENTS_FIELDS = ["year", "category_id", "amount", "file", "page", "x", "y", "width", "height"]

# All bbox coordinates in the CSVs are pre-scaled to the HTML viewer's canvas
# (PDF.js renders the page at 1.5× points). `pdftotext` returns raw PDF points,
# so we multiply every extracted bbox by this factor before writing.
# Convention is shared with fix_zero_coordinates.py (CANVAS_SCALE = 1.5).
CANVAS_SCALE = 1.5


@dataclass
class Spec:
    category_id: int
    page: int
    label_regex: str  # matched against a single line of layout text
    column: str = "current"  # "current" (2025) or "previous" (2024)
    negate: bool = False
    occurrence: int = 1  # 1-based for ambiguous labels


def run_pdftotext(pdf_path: Path, page: int, layout: bool) -> str:
    args = ["pdftotext", "-f", str(page), "-l", str(page)]
    if layout:
        args.append("-layout")
    args.extend([str(pdf_path), "-"])
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


def page_words(pdf_path: Path, page: int) -> List[Tuple[float, float, float, float, str]]:
    xml = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox-layout", str(pdf_path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    pattern = re.compile(
        r'<word xMin="([0-9.]+)" yMin="([0-9.]+)" xMax="([0-9.]+)" yMax="([0-9.]+)">(.*?)</word>'
    )
    out: List[Tuple[float, float, float, float, str]] = []
    for m in pattern.finditer(xml):
        x0, y0, x1, y1, raw = m.groups()
        text = html.unescape(raw).strip()
        if text:
            out.append((float(x0), float(y0), float(x1), float(y1), text))
    return out


def parse_amount(token: str) -> Optional[int]:
    cleaned = token.replace("−", "-").replace("–", "-").replace(" ", "").replace("\xa0", "")
    if cleaned in {"", "-"}:
        return 0 if cleaned == "-" else None
    if not re.fullmatch(r"-?\d+", cleaned):
        return None
    return int(cleaned)


AMOUNT_TOKEN_RE = re.compile(r"-?[\d  −–]+\d")


def extract_two_amounts(line: str) -> List[int]:
    """Return integers on the line, dropping leading note-reference digits."""
    candidates = []
    # Split by 2+ whitespace so adjacent column values stay separate.
    chunks = re.split(r"\s\s+", line)
    for chunk in chunks:
        if not chunk.strip():
            continue
        for cmatch in re.finditer(r"-?\d[\d ]*\d|\b-?\d\b", chunk):
            ctoken = cmatch.group(0).strip()
            cvalue = parse_amount(ctoken)
            if cvalue is not None:
                candidates.append(cvalue)
    if not candidates:
        return candidates
    first_significant = next(
        (i for i, v in enumerate(candidates) if len(str(abs(v))) >= 4),
        0,
    )
    return candidates[first_significant:]


def find_value_bbox(
    words: List[Tuple[float, float, float, float, str]],
    target_value: int,
    after_anchor: Optional[str] = None,
) -> Optional[Tuple[int, int, int, int]]:
    """Locate the bbox of a numeric token whose digits form ``target_value``.

    Searches the words list for sequences of digit tokens (possibly broken by
    thousand-separating spaces) that concatenate to the target's digit string.
    """
    target = str(abs(target_value))
    n = len(words)
    anchor_idx = -1
    if after_anchor:
        anchor_norm = after_anchor.lower()
        for i, w in enumerate(words):
            if w[4].lower().startswith(anchor_norm):
                anchor_idx = i
                break

    for i in range(max(0, anchor_idx), n):
        digits = ""
        boxes = []
        for j in range(i, min(n, i + 6)):
            tok = words[j][4]
            stripped = tok.replace("\xa0", "").replace(" ", "")
            # Allow leading minus sign.
            sign_match = re.match(r"^-(\d.*)$", stripped)
            digits_only = stripped.lstrip("-")
            if not digits_only.isdigit():
                break
            digits += digits_only
            boxes.append(words[j])
            if digits == target:
                x0 = min(b[0] for b in boxes)
                y0 = min(b[1] for b in boxes)
                x1 = max(b[2] for b in boxes)
                y1 = max(b[3] for b in boxes)
                return (
                    int(round(x0)),
                    int(round(y0)),
                    int(round(x1 - x0)),
                    int(round(y1 - y0)),
                )
            if not target.startswith(digits):
                break
    return None


def find_value_bbox_on_row(
    words: List[Tuple[float, float, float, float, str]],
    target_value: int,
    anchor_words: List[str],
) -> Optional[Tuple[int, int, int, int]]:
    """Find a token sequence matching ``target_value`` on the same y-row as the anchor.

    The anchor is matched as the *sequence* ``anchor_words`` so that ambiguous
    starter words (e.g. "Varav", "Övriga", "Ökning(-)/Minskning") still resolve
    to the correct row by chaining on the next 1–2 label tokens.
    """
    if not anchor_words:
        return None
    target_digits = str(abs(target_value))
    anchor_first = anchor_words[0]
    matches = [w for w in words if w[4] == anchor_first]
    if not matches:
        matches = [w for w in words if w[4].lower() == anchor_first.lower()]
    if not matches:
        return None

    chosen: Optional[Tuple[float, float, float, float, str]] = None
    if len(anchor_words) == 1:
        chosen = min(matches, key=lambda w: w[0])
    else:
        # Require that the next anchor_words appear immediately after the match
        # on the same y row.
        for cand in matches:
            y_min, y_max = cand[1], cand[3]
            band = (y_min - 1, y_max + 1)
            row_after = sorted(
                [w for w in words if w[3] > band[0] and w[1] < band[1] and w[0] > cand[2] - 1],
                key=lambda w: w[0],
            )
            ok = True
            for offset, expected in enumerate(anchor_words[1:], start=0):
                if offset >= len(row_after):
                    ok = False
                    break
                actual = row_after[offset][4]
                if actual != expected and actual.lower() != expected.lower():
                    ok = False
                    break
            if ok:
                chosen = cand
                break
        if chosen is None:
            chosen = min(matches, key=lambda w: w[0])

    anchor_y = (chosen[1], chosen[3])
    y_min, y_max = anchor_y
    # Use a narrow band to avoid catching tokens from the adjacent row.
    y_band = (y_min - 1, y_max + 1)

    row_words = [w for w in words if w[3] > y_band[0] and w[1] < y_band[1]]
    row_words.sort(key=lambda w: w[0])
    for i in range(len(row_words)):
        digits = ""
        boxes = []
        for j in range(i, min(len(row_words), i + 6)):
            tok = row_words[j][4]
            stripped = tok.replace("\xa0", "").replace(" ", "")
            digits_only = stripped.lstrip("-")
            if not digits_only.isdigit():
                break
            digits += digits_only
            boxes.append(row_words[j])
            if digits == target_digits:
                x0 = min(b[0] for b in boxes)
                y0 = min(b[1] for b in boxes)
                x1 = max(b[2] for b in boxes)
                y1 = max(b[3] for b in boxes)
                return (
                    int(round(x0)),
                    int(round(y0)),
                    int(round(x1 - x0)),
                    int(round(y1 - y0)),
                )
            if not target_digits.startswith(digits):
                break
    return None


def extract_value(
    pdf_path: Path,
    spec: Spec,
) -> Optional[Tuple[int, Tuple[int, int, int, int]]]:
    text = run_pdftotext(pdf_path, spec.page, layout=True)
    lines = text.splitlines()
    pattern = re.compile(spec.label_regex)
    matches = [line for line in lines if pattern.search(line)]
    if len(matches) < spec.occurrence:
        return None
    line = matches[spec.occurrence - 1]
    amounts = extract_two_amounts(line)
    if not amounts:
        return None
    if len(amounts) == 1:
        # Single-column tables (e.g. Not 15 with "-" placeholder for prior).
        value = amounts[0]
    else:
        value = amounts[0] if spec.column == "current" else amounts[1]
    if spec.negate:
        value = -abs(value)
    words = page_words(pdf_path, spec.page)
    # Tokenise the label line the same way pdftotext does for -bbox-layout
    # (whitespace-separated). This matches compound words like
    # "Förvärv/avyttring" or "Ökning(-)/Minskning" against their PDF tokens.
    raw_tokens = line.lstrip().split()
    label_tokens: List[str] = []
    for token in raw_tokens:
        if re.fullmatch(r"-?[\d  \xa0]+", token):
            break
        if token.isdigit() or (len(token) <= 2 and token.isdigit()):
            break
        label_tokens.append(token)
    if not label_tokens:
        return None
    anchor_seq = label_tokens[: min(6, len(label_tokens))]
    bbox = find_value_bbox_on_row(words, value, anchor_seq)
    if bbox is None:
        return None
    # Scale to the HTML viewer's canvas units (see CANVAS_SCALE).
    x, y, w, h = bbox
    bbox = (
        int(round(x * CANVAS_SCALE)),
        int(round(y * CANVAS_SCALE)),
        int(round(w * CANVAS_SCALE)),
        int(round(h * CANVAS_SCALE)),
    )
    return value, bbox


# Mapping for stamma2026.pdf (finance period 2025).
# Page numbers are PDF page indices, not the printed sida numbers.
SPECS_STATES: List[Spec] = [
    # Short-term portion of loans is stored under category 0; long-term under 1
    # (the labels in state_categories.csv are swapped relative to the data —
    # historical convention; see commit c4ea045 "fixed mixed up short and long
    # term loans" for the data side of that swap).
    Spec(0, 21, r"^\s*Varav\s+Kortfristig\s+del"),
    Spec(1, 21, r"^\s*Varav\s+Långfristig\s+del"),
    # Likvida medel total (matches the 2024 convention which stored the total,
    # not the bare "Kassa och bank" line).
    Spec(2, 16, r"^\s*Likvida\s+medel\s+vid\s+årets\s+slut"),
    Spec(6, 15, r"^\s*Summa\s+eget\s+kapital"),
    Spec(7, 14, r"^\s*SUMMA\s+TILLGÅNGAR"),
]

SPECS_EVENTS: List[Spec] = [
    # Resultaträkning (income statement) — PDF page 13.
    Spec(0, 13, r"^\s*Nettoomsättning\b"),
    Spec(1, 13, r"^\s*Driftkostnader\b", negate=True),
    Spec(4, 13, r"^\s*Övriga\s+rörelseintäkter\b"),
    Spec(5, 13, r"^\s*Övriga\s+externa\s+kostnader\b", negate=True),
    Spec(6, 13, r"^\s*Personalkostnader\b", negate=True),
    # Kassaflödesanalys — PDF page 16.
    Spec(7, 16, r"^\s*Erlagd\s+ränta"),
    Spec(8, 16, r"^\s*Förvärv/avyttring\s+av\s+materiella"),
    Spec(3, 16, r"^\s*Kassaflöde\s+från\s+finansieringsverksamheten"),
    Spec(11, 16, r"av\s+leverantörsskulder"),
    Spec(12, 16, r"av\s+övr\.\s*kortfristiga\s+skulder"),
    Spec(13, 16, r"av\s+kundfordringar"),
    Spec(14, 16, r"av\s+fordringar\b"),
    # Notes 2 (Nettoomsättning) and Not 4 (Driftskostnader) — PDF page 18.
    Spec(17, 18, r"^\s*Årsavgifter\b"),
    Spec(19, 18, r"^\s*Överlåtelse-\s*och\s+pantsättningsavgifter"),
    Spec(21, 18, r"^\s*Reparationer\b", negate=True),
    Spec(22, 18, r"^\s*El\b", negate=True),
    Spec(23, 18, r"^\s*Uppvärmning\b", negate=True),
    Spec(24, 18, r"^\s*Vatten\b", negate=True),
    Spec(25, 18, r"^\s*Sophämtning\b", negate=True),
    Spec(26, 18, r"^\s*Försäkringspremie\b", negate=True),
    Spec(27, 18, r"^\s*Kabel-tv/Bredband/IT\b", negate=True),
    Spec(29, 18, r"^\s*Förvaltningsavtalskostnader\b", negate=True),
    Spec(31, 18, r"^\s*Underhåll\b", negate=True),
    Spec(50, 18, r"^\s*Fastighetsskötsel\b", negate=True),
    Spec(51, 18, r"^\s*Städning\b", negate=True),
    Spec(52, 18, r"^\s*Hisstillsyn\b", negate=True),
    Spec(53, 18, r"^\s*Tillsyn,\s+besiktning,\s+kontroller", negate=True),
    Spec(54, 18, r"^\s*Trädgårdsskötsel\b", negate=True),
    Spec(55, 18, r"^\s*Snöröjning\b", negate=True),
    # Övriga externa kostnader (Not 5) and Personalkostnader (Not 6) — PDF page 19.
    Spec(34, 19, r"^\s*Hyror\s+och\s+leasing", negate=True),
    Spec(35, 19, r"^\s*Förbrukningsinventarier", negate=True),
    Spec(36, 19, r"^\s*Administrationskostnader", negate=True),
    Spec(37, 19, r"^\s*Revisionsarvode", negate=True),
    Spec(39, 19, r"^\s*Medlemsavgifter", negate=True),
    Spec(42, 19, r"^\s*Övriga\s+förvaltningskostnader", negate=True),
    Spec(43, 19, r"^\s*Underhållsplan\b", negate=True),
    Spec(44, 19, r"^\s*Styrelsearvoden\b", negate=True),
    Spec(46, 19, r"^\s*Övriga\s+arvoden\b", negate=True),
    Spec(47, 19, r"^\s*Sociala\s+kostnader\b", negate=True),
]


# Synthetic sub-parents — categories that are not printed as a single line in
# the modern PDF but exist in event_categories.csv as the parent of a known
# leaf set. They are derived as the sum of their children after the regex
# pass so the hierarchy validation closes. Maps parent_id -> [child_ids].
# The bbox is computed from the union of the children's already-scaled bboxes,
# so highlight overlays land on the correct group of leaf rows.
SYNTHETIC_SUBPARENTS: dict = {
    # Fastighetsskötsel och lokalvård — printed only as separate Drift line
    # items in the modern Not 4. Children 50–55 are the canonical leaves.
    2: [50, 51, 52, 53, 54, 55],
}


# Aggregated leaf categories — single category_ids whose value is the sum of
# several distinct PDF lines (no single label matches). Each entry lists the
# Specs whose values should be summed; the bbox is the union of those matches.
AGGREGATED_SPECS: dict = {
    # Hyror (cat 18) — Not 2 lists 4 separate hyror lines without a subtotal.
    18: [
        Spec(18, 18, r"^\s*Hyror\s+bostäder\b"),
        Spec(18, 18, r"^\s*Hyror\s+garage\b"),
        Spec(18, 18, r"^\s*Hyror\s+lokaler\b"),
        Spec(18, 18, r"^\s*Hyror\s+övrigt\b"),
    ],
    # Fastighetsskatt och fastighetsavgift (cat 28) — Not 4 splits this into
    # the residential property fee + the commercial property tax line.
    28: [
        Spec(28, 18, r"^\s*Fastighetsavgift\s+bostäder\b", negate=True),
        Spec(28, 18, r"^\s*Fastighetsskatt\s+lokaler\b", negate=True),
    ],
}


def derive_synthetic_subparents(event_rows: List[dict], year: int, pdf_file: str) -> List[dict]:
    """Append synthetic sub-parent rows (e.g. cat 2 = Σ50..55) so the cat 1
    hierarchy validation passes. Children must all be present; if any is
    missing the parent is skipped (a missing leaf is its own problem to fix).
    """
    by_cat = {int(r["category_id"]): r for r in event_rows if r["year"] == str(year)}
    synthesized: List[dict] = []
    for parent_id, child_ids in SYNTHETIC_SUBPARENTS.items():
        if parent_id in by_cat:
            continue
        if not all(c in by_cat for c in child_ids):
            continue
        total = sum(int(by_cat[c]["amount"]) for c in child_ids)
        children_rows = [by_cat[c] for c in child_ids]
        pages = {int(r["page"]) for r in children_rows}
        page = sorted(pages)[0]  # most-common; fall back to lowest if split
        xs = [int(r["x"]) for r in children_rows]
        ys = [int(r["y"]) for r in children_rows]
        x_rights = [int(r["x"]) + int(r["width"]) for r in children_rows]
        y_bottoms = [int(r["y"]) + int(r["height"]) for r in children_rows]
        x0, y0 = min(xs), min(ys)
        synthesized.append({
            "year": str(year),
            "category_id": str(parent_id),
            "amount": str(total),
            "file": pdf_file,
            "page": str(page),
            "x": str(x0),
            "y": str(y0),
            "width": str(max(x_rights) - x0),
            "height": str(max(y_bottoms) - y0),
        })
    return synthesized


def upsert_row(rows: List[dict], new_row: dict) -> None:
    key = (new_row["year"], new_row["category_id"])
    for i, row in enumerate(rows):
        if (row["year"], row["category_id"]) == key:
            rows[i] = new_row
            return
    rows.append(new_row)


def load_rows(path: Path, fieldnames: List[str]) -> List[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return [{k: row.get(k, "") for k in fieldnames} for row in csv.DictReader(f)]


def detect_line_terminator(path: Path) -> str:
    """Sniff the existing file's line terminator so we preserve it on rewrite."""
    if not path.exists():
        return "\n"
    with path.open("rb") as f:
        sample = f.read(8192)
    if b"\r\n" in sample:
        return "\r\n"
    return "\n"


def write_rows(path: Path, rows: List[dict], fieldnames: List[str]) -> None:
    terminator = detect_line_terminator(path)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator=terminator)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


EVENT_CATEGORIES_CSV = Path("data/event_categories.csv")


# State-category IDs in the convention used by financial_states.csv. Codified
# here so reconciliations stay stable even if the human-readable names in
# state_categories.csv keep their historical short/long-loan label swap.
STATE_LOAN_SHORT = 0
STATE_LOAN_LONG = 1
STATE_CASH = 2
STATE_EQUITY = 6
STATE_TOTAL_ASSETS = 7

# Event-category IDs that feed the cross-table identities below.
EVENT_NETTOOMSATTNING = 0
EVENT_DRIFTKOSTNADER = 1
EVENT_FINANSIERING = 3
EVENT_OVRIGA_INTAKTER = 4
EVENT_OVRIGA_EXTERNA = 5
EVENT_PERSONALKOSTNADER = 6
EVENT_ERLAGD_RANTA = 7
EVENT_INVESTERINGAR = 8
EVENT_WC_LEVERANTOR = 11
EVENT_WC_OVR_KORTFR = 12
EVENT_WC_KUND = 13
EVENT_WC_OVR_FORDR = 14

# Tolerance for accrual-vs-cash rounding (net interest can move by a few SEK
# between the cash-flow value and the strict P&L value).
RECONCILIATION_TOLERANCE = 5


def load_event_parent_map() -> dict:
    """Map child category_id -> parent category_id (from data/event_categories.csv)."""
    parents: dict = {}
    with EVENT_CATEGORIES_CSV.open("r", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            cid = int(r["id"])
            raw = r.get("parent", "").strip()
            parents[cid] = int(raw) if raw else None
    return parents


def event_amounts_for_year(events: List[dict], year: int) -> dict:
    return {int(r["category_id"]): int(r["amount"]) for r in events if r["year"] == str(year)}


def state_amounts_for_year(states: List[dict], year: int) -> dict:
    return {int(r["category_id"]): int(r["amount"]) for r in states if r["year"] == str(year)}


def validate_event_hierarchy(events_for_year: dict, parent_map: dict) -> List[str]:
    """Return list of diagnostic strings; one per parent whose children disagree.

    A parent is considered consistent if every child either (a) is present in
    the events dict and its value contributes to the running sum, or (b) is
    absent and treated as zero. Missing children that would have non-zero
    values still surface as a discrepancy via the parent — sum mismatch.
    """
    from collections import defaultdict

    diagnostics: List[str] = []
    children_of: dict = defaultdict(list)
    for cid, pid in parent_map.items():
        if pid is not None:
            children_of[pid].append(cid)

    for parent_id in sorted(children_of):
        if parent_id not in events_for_year:
            continue
        present_children = [c for c in children_of[parent_id] if c in events_for_year]
        if not present_children:
            continue
        parent_val = events_for_year[parent_id]
        child_sum = sum(events_for_year[c] for c in present_children)
        diff = parent_val - child_sum
        if abs(diff) > RECONCILIATION_TOLERANCE:
            missing = [c for c in children_of[parent_id] if c not in events_for_year]
            diagnostics.append(
                f"cat {parent_id} = {parent_val:,}; "
                f"Σ children {sorted(present_children)} = {child_sum:,}; "
                f"diff = {diff:,}; "
                f"missing children = {sorted(missing)}"
            )
    return diagnostics


def validate_state_event_reconciliation(
    prior_states: dict,
    curr_states: dict,
    curr_events: dict,
) -> List[Tuple[str, int, int, int]]:
    """Return list of (check_name, expected, actual, diff).

    Two identities checked (both must reconcile to within tolerance):
      A) Δ(short loan + long loan)  =  event Kassaflöde finansieringsverksamheten
      B) Δ(likvida medel)           =  Σ cash-flow events (excl. avskrivning)

    Note: avskrivning is *not* stored as an event (it is non-cash and cancels
    between P&L and kassaflöde), so a direct equity-vs-årets-resultat check
    cannot be derived from the stored events alone — we don't include it.
    """
    results: List[Tuple[str, int, int, int]] = []

    # A) loan total delta vs Kassaflöde finans (cat 3).
    if (
        all(k in prior_states and k in curr_states for k in (STATE_LOAN_SHORT, STATE_LOAN_LONG))
        and EVENT_FINANSIERING in curr_events
    ):
        d_loan = (
            curr_states[STATE_LOAN_SHORT] + curr_states[STATE_LOAN_LONG]
            - prior_states[STATE_LOAN_SHORT] - prior_states[STATE_LOAN_LONG]
        )
        actual = curr_events[EVENT_FINANSIERING]
        results.append(("Δloans == finansiering (event 3)", d_loan, actual, d_loan - actual))

    # B) Δcash vs Σ cash-flow events. Avskrivning is non-cash and not stored;
    # it cancels exactly between the P&L items (where it would be subtracted)
    # and the kassaflöde "addback" — so summing the stored events directly
    # gives the cash flow without needing avskrivning as a separate term.
    cash_event_keys = (
        EVENT_NETTOOMSATTNING, EVENT_OVRIGA_INTAKTER,
        EVENT_DRIFTKOSTNADER, EVENT_OVRIGA_EXTERNA, EVENT_PERSONALKOSTNADER,
        EVENT_ERLAGD_RANTA, EVENT_INVESTERINGAR, EVENT_FINANSIERING,
        EVENT_WC_LEVERANTOR, EVENT_WC_OVR_KORTFR, EVENT_WC_KUND, EVENT_WC_OVR_FORDR,
    )
    if (
        STATE_CASH in prior_states
        and STATE_CASH in curr_states
        and all(k in curr_events for k in cash_event_keys)
    ):
        d_cash = curr_states[STATE_CASH] - prior_states[STATE_CASH]
        actual = sum(curr_events[k] for k in cash_event_keys)
        results.append(("Δcash == Σ cash-flow events", d_cash, actual, d_cash - actual))

    return results


def print_validations(year: int, state_rows: List[dict], event_rows: List[dict]) -> int:
    """Run both validations against the merged (existing+new) CSVs. Return nonzero if any check fails."""
    parent_map = load_event_parent_map()

    # Merge new rows with existing for the prior-year lookup.
    states = load_rows(STATES_CSV, STATES_FIELDS)
    for row in state_rows:
        upsert_row(states, row)
    events = load_rows(EVENTS_CSV, EVENTS_FIELDS)
    for row in event_rows:
        upsert_row(events, row)

    e_curr = event_amounts_for_year(events, year)
    s_curr = state_amounts_for_year(states, year)
    s_prior = state_amounts_for_year(states, year - 1)

    print("\n=== validation: event hierarchy ===")
    hier_issues = validate_event_hierarchy(e_curr, parent_map)
    if not hier_issues:
        print("  OK (all parents reconcile with their children within tolerance)")
    else:
        for issue in hier_issues:
            print(f"  WARN {issue}")

    print(f"\n=== validation: state vs event identities ({year - 1} → {year}) ===")
    if not s_prior:
        print(f"  SKIP (no prior-year state rows for {year - 1})")
        return len(hier_issues)
    recon = validate_state_event_reconciliation(s_prior, s_curr, e_curr)
    if not recon:
        print("  SKIP (insufficient categories for any identity)")
    fail = 0
    for name, expected, actual, diff in recon:
        status = "OK   " if abs(diff) <= RECONCILIATION_TOLERANCE else "FAIL "
        if abs(diff) > RECONCILIATION_TOLERANCE:
            fail += 1
        print(f"  {status}{name}: expected={expected:>13,}  actual={actual:>13,}  diff={diff:>10,}")

    return len(hier_issues) + fail


def build_aggregated_rows(pdf_file: str, year: int) -> Tuple[List[dict], List[int]]:
    """Build rows for AGGREGATED_SPECS — values summed across several PDF lines."""
    pdf_path = PDF_DIR / pdf_file
    out_rows: List[dict] = []
    failed: List[int] = []
    for cat_id, sub_specs in AGGREGATED_SPECS.items():
        partials = []
        for spec in sub_specs:
            result = extract_value(pdf_path, spec)
            if result is None:
                continue
            partials.append(result)
        if not partials:
            failed.append(cat_id)
            continue
        total = sum(v for v, _ in partials)
        xs = [b[0] for _, b in partials]
        ys = [b[1] for _, b in partials]
        x_rights = [b[0] + b[2] for _, b in partials]
        y_bottoms = [b[1] + b[3] for _, b in partials]
        x0, y0 = min(xs), min(ys)
        # Use the page from the first matched sub-spec.
        page = sub_specs[0].page
        out_rows.append({
            "year": str(year),
            "category_id": str(cat_id),
            "amount": str(total),
            "file": pdf_file,
            "page": str(page),
            "x": str(x0),
            "y": str(y0),
            "width": str(max(x_rights) - x0),
            "height": str(max(y_bottoms) - y0),
        })
    return out_rows, failed


def build_rows(pdf_file: str, year: int, specs: List[Spec]) -> Tuple[List[dict], List[Spec]]:
    pdf_path = PDF_DIR / pdf_file
    out_rows: List[dict] = []
    failed: List[Spec] = []
    for spec in specs:
        result = extract_value(pdf_path, spec)
        if result is None:
            failed.append(spec)
            continue
        value, (x, y, width, height) = result
        out_rows.append(
            {
                "year": str(year),
                "category_id": str(spec.category_id),
                "amount": str(value),
                "file": pdf_file,
                "page": str(spec.page),
                "x": str(x),
                "y": str(y),
                "width": str(width),
                "height": str(height),
            }
        )
    return out_rows, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract modern-format financial rows")
    parser.add_argument("year", type=int)
    parser.add_argument("pdf", help="Filename under data/annual_reports/")
    parser.add_argument("--append", action="store_true")
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip the post-extraction reconciliation checks (hierarchy + state/event identities).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any reconciliation check fails or any spec fails to extract.",
    )
    args = parser.parse_args()

    state_rows, state_failed = build_rows(args.pdf, args.year, SPECS_STATES)
    event_rows, event_failed = build_rows(args.pdf, args.year, SPECS_EVENTS)
    aggregated_rows, aggregated_failed = build_aggregated_rows(args.pdf, args.year)
    event_rows.extend(aggregated_rows)
    synthetic_event_rows = derive_synthetic_subparents(event_rows, args.year, args.pdf)
    event_rows.extend(synthetic_event_rows)

    print(f"financial_states: extracted={len(state_rows)} failed={len(state_failed)}")
    for spec in state_failed:
        print(f"  FAIL state cat={spec.category_id} page={spec.page} regex={spec.label_regex!r}")

    print(
        f"financial_events: extracted={len(event_rows)} failed={len(event_failed)} "
        f"(incl. {len(aggregated_rows)} aggregated, {len(synthetic_event_rows)} synthetic sub-parents)"
    )
    for spec in event_failed:
        print(f"  FAIL event cat={spec.category_id} page={spec.page} regex={spec.label_regex!r}")
    for cat in aggregated_failed:
        print(f"  FAIL aggregated cat={cat} — none of the sub-line regexes matched")
    for row in aggregated_rows:
        print(f"  AGG  event cat={row['category_id']} amount={row['amount']} (sum of sub-lines)")
    for row in synthetic_event_rows:
        print(f"  SYN  event cat={row['category_id']} amount={row['amount']} (derived from children)")

    print("\n# financial_states rows:")
    for row in state_rows:
        print(",".join(row[k] for k in STATES_FIELDS))
    print("\n# financial_events rows:")
    for row in event_rows:
        print(",".join(row[k] for k in EVENTS_FIELDS))

    if args.append:
        existing_states = load_rows(STATES_CSV, STATES_FIELDS)
        for row in state_rows:
            upsert_row(existing_states, row)
        write_rows(STATES_CSV, existing_states, STATES_FIELDS)

        existing_events = load_rows(EVENTS_CSV, EVENTS_FIELDS)
        for row in event_rows:
            upsert_row(existing_events, row)
        write_rows(EVENTS_CSV, existing_events, EVENTS_FIELDS)
        print(f"Upserted into {STATES_CSV} and {EVENTS_CSV}")

    validation_fail = 0
    if not args.skip_validation:
        validation_fail = print_validations(args.year, state_rows, event_rows)

    if args.strict and (state_failed or event_failed or validation_fail):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
