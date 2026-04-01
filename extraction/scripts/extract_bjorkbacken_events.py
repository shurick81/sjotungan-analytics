#!/usr/bin/env python3
"""Extract financial events from Björkbacken annual report to CSV.

Reads the PDF with pdfplumber, locates amount coordinates,
and writes data/financial_events_bjorkbacken.csv.
"""

import csv
import sys
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    sys.exit("pdfplumber required: pip install pdfplumber")

ROOT = Path(__file__).resolve().parent.parent.parent
PDF_NAME = "HSB Brf Bjorkbacken i Tyreso AR och RB 2024.pdf"
PDF_PATH = ROOT / "data" / "annual_reports" / PDF_NAME
OUT_PATH = ROOT / "data" / "financial_events_bjorkbacken.csv"

# ── Verified amounts for fiscal year 2024 ────────────────────────
# All amounts are signed: positive = inflow, negative = outflow.
# category_id → (amount, search_page_1indexed, label_hint)

EVENTS = {
    # ── Income Statement (page 9) ──
    0:  (34_346_845,  9, "Nettoomsättning"),
    4:  (462_259,     9, "Övriga rörelseintäkter"),
    1:  (-19_808_147, 9, "Driftkostnader"),
    5:  (-137_972,    9, "Övriga externa kostnader"),
    6:  (-620_309,    9, "Personalkostnader"),
    15: (198_706,     9, "Ränteintäkter"),
    7:  (-3_798_051,  9, "Räntekostnader"),

    # ── Cash-flow working capital (page 12) ──
    13: (-36_196,    12, "kundfordringar"),
    14: (204_743,    12, "fordringar"),
    11: (-1_858_797, 12, "leverantörsskulder"),
    12: (716_545,    12, "kortfristiga skulder"),

    # ── Cash-flow investing (page 12) ──
    8:  (-19_220_721, 12, "Investeringar"),

    # ── Cash-flow financing (page 12) ──
    3:  (9_369_112,   12, "finansieringsverksamheten"),

    # ── Revenue sub-categories (Not 2, page 15) ──
    # These are aggregated; use primary component for coord lookup
    17: (31_809_836, 15, "Årsavgifter"),        # 30,600,984 + 1,208,852 el
    18: (1_947_884,  15, "Hyror"),               # 443,890 + 1,503,994
    19: (589_125,    15, "IT/Bredband"),         # 574,500 + 14,625 övrigt

    # ── Drift sub-categories (Not 4, pages 15-16) ──
    50: (-704_912,   15, "Fastighetsskötsel"),
    51: (-469_615,   15, "Städning"),
    53: (-177_656,   15, "Tillsyn"),
    54: (-2_313_754, 15, "Trädgårdsskötsel"),
    55: (-250_522,   15, "Snöröjning"),
    21: (-1_279_021, 15, "Reparationer"),
    22: (-2_116_293, 15, "El"),
    23: (-5_286_382, 15, "Uppvärmning"),
    24: (-2_278_946, 15, "Vatten"),
    25: (-693_223,   15, "Sophämtning"),
    26: (-595_371,   15, "Försäkringspremie"),
    28: (-711_400,   15, "Fastighetsavgift"),    # 624,290 + 87,110
    30: (-138_712,   15, "Övriga fastighetskostnader"),
    27: (-804_651,   15, "Kabel-tv"),
    29: (-1_065_465, 15, "Förvaltningsarvode"),  # sum of 7 management line items
    31: (-922_224,   16, "Underhåll"),            # 86,875+277,411+489,813+68,125

    # ── Övriga externa sub-categories (Not 5, page 16) ──
    36: (-6_933,    16, "Porto"),
    38: (-41_789,   16, "Konsultarvode"),
    42: (-14_500,   16, "Besiktnings"),
    37: (-74_750,   16, "Revisionarvode"),

    # ── Personalkostnader sub-categories (Not 6, page 16) ──
    44: (-495_000,  16, "Arvode styrelse"),
    46: (-27_725,   16, "Arvode intern revisor"),
    47: (-97_584,   16, "Sociala kostnader"),
}

# ── Sanity checks ──
def verify():
    cats = {}
    cat_csv = ROOT / "data" / "event_categories.csv"
    for row in csv.DictReader(cat_csv.open(encoding="utf-8")):
        cats[int(row["id"])] = row

    top_ids = {cid for cid in EVENTS if not cats[cid].get("parent", "").strip()}
    top_sum = sum(EVENTS[cid][0] for cid in top_ids)
    expected_cash_change = -181_983
    assert top_sum == expected_cash_change, (
        f"Top-level sum {top_sum:,} != expected cash change {expected_cash_change:,}"
    )

    # Revenue subs must sum to nettoomsättning
    rev_subs = sum(EVENTS[cid][0] for cid in (17, 18, 19))
    assert rev_subs == EVENTS[0][0], f"Rev subs {rev_subs:,} != {EVENTS[0][0]:,}"

    # Drift sub-sums
    # cat 2 subs (50,51,53,54,55)
    cat2_subs = sum(EVENTS[cid][0] for cid in (50, 51, 53, 54, 55))
    # All parent=1 subs: cat2 total + 21-31
    drift_sub_ids = [50, 51, 53, 54, 55, 21, 22, 23, 24, 25, 26, 28, 30, 27, 29, 31]
    drift_sub_sum = sum(EVENTS[cid][0] for cid in drift_sub_ids)
    assert drift_sub_sum == EVENTS[1][0], (
        f"Drift subs {drift_sub_sum:,} != {EVENTS[1][0]:,}"
    )

    # Övriga externa subs
    ext_sub_sum = sum(EVENTS[cid][0] for cid in (36, 38, 42, 37))
    assert ext_sub_sum == EVENTS[5][0], (
        f"Ext subs {ext_sub_sum:,} != {EVENTS[5][0]:,}"
    )

    # Personal subs
    pers_sub_sum = sum(EVENTS[cid][0] for cid in (44, 46, 47))
    assert pers_sub_sum == EVENTS[6][0], (
        f"Personal subs {pers_sub_sum:,} != {EVENTS[6][0]:,}"
    )
    print("All sanity checks passed.")


# ── Coordinate extraction ──
SCALE = 72 / 300 * 1.5  # 0.36 – converts 300-dpi pixel coords to canvas


def find_amount_coords(pdf_page, amount, label_hint):
    """Search a pdfplumber page for a word matching |amount| and return canvas coords."""
    target = f"{abs(amount):,}".replace(",", " ")  # e.g. "19 808 147"
    # Also try without thousands separator
    target_plain = str(abs(amount))

    words = pdf_page.extract_words(x_tolerance=3, y_tolerance=3)
    page_text = " ".join(w["text"] for w in words)

    # Try to find the amount as a contiguous string in words
    # Amounts in PDFs often appear as separate digit groups
    # Strategy: scan consecutive words and check if they form the amount
    for i, w in enumerate(words):
        combined = w["text"].replace(" ", "").replace("\xa0", "")
        x0, y0, x1, y1 = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])

        # Try combining with subsequent words
        j = i
        while j < len(words) - 1:
            j += 1
            nw = words[j]
            # Must be on same line (y within 5pt)
            if abs(float(nw["top"]) - y0) > 5:
                break
            combined += nw["text"].replace(" ", "").replace("\xa0", "")
            x1 = float(nw["x1"])
            y1 = max(y1, float(nw["bottom"]))
            cleaned = combined.replace(".", "").replace(",", "").replace("-", "").replace("\u2212", "")
            if cleaned == target_plain:
                # Found it – convert to canvas coords
                cx = round(x0 * SCALE)
                cy = round(y0 * SCALE)
                cw = round((x1 - x0) * SCALE)
                ch = round((y1 - y0) * SCALE)
                return cx, cy, cw, ch

    # Fallback: scan for the plain digits
    for i, w in enumerate(words):
        t = w["text"].replace(" ", "").replace("\xa0", "").replace(".", "").replace(",", "").replace("-", "").replace("\u2212", "")
        if t == target_plain:
            x0, y0, x1, y1 = float(w["x0"]), float(w["top"]), float(w["x1"]), float(w["bottom"])
            return (round(x0 * SCALE), round(y0 * SCALE),
                    round((x1 - x0) * SCALE), round((y1 - y0) * SCALE))

    print(f"  WARNING: could not locate amount {amount:,} (hint: {label_hint})")
    return 0, 0, 0, 0


# For aggregated amounts that don't appear literally in the PDF,
# use the primary component amount to find coordinates instead.
COORD_FALLBACKS = {
    17: 30_600_984,   # Årsavgifter (main line, excl el-avgifter)
    18: 443_890,      # Hyror lokaler (first hyra line)
    19: 574_500,      # IT/Bredband (main component)
    28: 624_290,      # Fastighetsavgift bostäder (main component)
    29: 349_232,      # Förvaltningsarvode ekonomi (main component)
}


def extract():
    verify()

    pdf = pdfplumber.open(str(PDF_PATH))
    pages = {p: pdf.pages[p - 1] for p in {v[1] for v in EVENTS.values()}}

    rows = []
    for cat_id, (amount, page_num, label) in sorted(EVENTS.items()):
        pg = pages[page_num]
        x, y, w, h = find_amount_coords(pg, amount, label)
        if w == 0 and cat_id in COORD_FALLBACKS:
            x, y, w, h = find_amount_coords(pg, COORD_FALLBACKS[cat_id], label)
        rows.append({
            "year": 2024,
            "category_id": cat_id,
            "amount": amount,
            "file": PDF_NAME,
            "page": page_num,
            "x": x,
            "y": y,
            "width": w,
            "height": h,
        })
        status = "OK" if w > 0 else "NO_COORDS"
        print(f"  cat {cat_id:>2} {label:<35} {amount:>14,}  p{page_num}  {status}")

    pdf.close()

    # Sort by page then y-position for readability
    rows.sort(key=lambda r: (r["page"], r["y"], r["category_id"]))

    fieldnames = ["year", "category_id", "amount", "file", "page", "x", "y", "width", "height"]
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUT_PATH}")


if __name__ == "__main__":
    extract()
