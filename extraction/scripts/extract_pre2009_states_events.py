#!/usr/bin/env python3
"""Extract legacy pre-2009 states/events from annual reports.

This script backfills years that are available in source PDFs but missing in
CSV datasets. It focuses on high-confidence rows (core states + top-level
events) for 2003, 2004, 2006, 2007 and 2008.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

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
    source_column: str
    items: Tuple[Item, ...]


EVENT_CONFIGS: Dict[int, Config] = {
    2005: Config(
        pdf="bokslut2005.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 36014818, 5, "36014818"),
            Item(1, -21172351, 5, "21172351"),
            Item(16, -2704441, 5, "2704441"),
            Item(15, 428562, 5, "428562"),
            Item(7, -4803181, 5, "4803181"),
            Item(8, -7181746, 8, "7181746"),
            Item(3, 3798990, 8, "3798990"),
        ),
    ),
    2008: Config(
        pdf="Arsredovisning_2008.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 37356786, 6, "37356786"),
            Item(1, -21630912, 6, "21630912"),
            Item(16, -1611853, 6, "1611853"),
            Item(15, 418330, 6, "418330"),
            Item(7, -6623666, 6, "6623666"),
            Item(8, -35235415, 9, "35235415"),
            Item(3, 35050356, 9, "35050356"),
        ),
    ),
    2007: Config(
        pdf="Arsredovisning_2007.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 37526958, 5, "37526958"),
            Item(1, -21156499, 5, "21156499"),
            Item(16, -1434274, 5, "1434274"),
            Item(15, 657339, 5, "657339"),
            Item(7, -4274759, 5, "4274759"),
            Item(8, -37191299, 8, "37191299"),
            Item(3, 18757096, 8, "18757096"),
        ),
    ),
    2006: Config(
        pdf="Arsredovisning_2006.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 36710185, 5, "36710185"),
            Item(1, -21234546, 5, "21234546"),
            Item(16, -325992, 5, "325992"),
            Item(15, 537594, 5, "537594"),
            Item(7, -4351630, 5, "4351630"),
            Item(8, -8240993, 7, "8240993"),
            Item(3, 3810616, 7, "3810616"),
        ),
    ),
    2004: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 35360328, 5, "35360328"),
            Item(1, -19125703, 5, "19125703"),
            Item(16, -7331735, 5, "7331735"),
            Item(15, 602038, 5, "602038"),
            Item(7, -4958656, 5, "4958656"),
            Item(3, 5167960, 8, "5167960"),
        ),
    ),
    2003: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(0, 34307289, 5, "34307289"),
            Item(1, -22998305, 5, "22998305"),
            Item(16, -2308877, 5, "2308877"),
            Item(15, 1032765, 5, "1032765"),
            Item(7, -4815219, 5, "4815219"),
            Item(3, -2549556, 8, "2549556"),
        ),
    ),
}


EVENT_SUBCATEGORY_CONFIGS: Dict[int, Config] = {
    2012: Config(
        pdf="arsredovisning_2013.pdf",
        min_left_px=1400,
        col_split_px=1900,
        source_column="previous",
        items=(
            Item(2, -3822596, 12, "3822596"),
            Item(21, -1830963, 12, "1830963"),
            Item(22, -2493583, 12, "2493583"),
            Item(23, -3556864, 12, "3556864"),
            Item(24, -3139458, 12, "3139458"),
            Item(25, -1260203, 12, "1260203"),
            Item(26, -397660, 12, "397660"),
            Item(27, -454888, 12, "454888"),
            Item(28, -247201, 12, "247201"),
            Item(29, -804179, 12, "804179"),
            Item(30, -893199, 12, "893199"),
        ),
    ),
    2011: Config(
        pdf="arsredovisning2012.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(2, -3277255, 13, "3277255"),
            Item(21, -1865456, 13, "1865456"),
            Item(22, -1986140, 13, "1986140"),
            Item(23, -3697628, 13, "3697628"),
            Item(24, -2698553, 13, "2698553"),
            Item(25, -1303080, 13, "1303080"),
            Item(26, -374707, 13, "374707"),
            Item(27, -447992, 13, "447992"),
            Item(28, -392776, 13, "392776"),
            Item(29, -775505, 13, "775505"),
            Item(30, -674274, 13, "674274"),
        ),
    ),
    2010: Config(
        pdf="arsredovisning2010.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(2, -4000006, 12, "4000006"),
            Item(21, -1668273, 12, "1668273"),
            Item(22, -2753411, 12, "2753411"),
            Item(23, -5857994, 12, "5857994"),
            Item(24, -3158996, 12, "3158996"),
            Item(25, -1314370, 12, "1314370"),
            Item(26, -380000, 12, "380000"),
            Item(27, -432536, 12, "432536"),
            Item(28, -396608, 12, "396608"),
            Item(29, -781827, 12, "781827"),
            Item(30, -533504, 12, "533504"),
        ),
    ),
    2009: Config(
        pdf="arsredovisning_2009.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(2, -3073838, 12, "3073838"),
            Item(21, -3294239, 12, "3294239"),
            Item(22, -2058469, 12, "2058469"),
            Item(23, -5990647, 12, "5990647"),
            Item(24, -2445389, 12, "2445389"),
            Item(25, -1235407, 12, "1235407"),
            Item(26, -341605, 12, "341605"),
            Item(27, -425688, 12, "425688"),
            Item(28, -356158, 12, "356158"),
            Item(29, -734554, 12, "734554"),
            Item(30, -991355, 12, "991355"),
        ),
    ),
    2008: Config(
        pdf="Arsredovisning_2008.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(2, -4056288, 11, "4056288"),
            Item(21, -2940245, 11, "2940245"),
            Item(22, -2188558, 11, "2188558"),
            Item(23, -5470350, 11, "5470350"),
            Item(24, -2572755, 11, "2572755"),
            Item(25, -1191742, 11, "1191742"),
            Item(26, -262201, 11, "262201"),
            Item(27, -413248, 11, "413248"),
            Item(28, -326304, 11, "326304"),
            Item(29, -780805, 11, "780805"),
            Item(30, -904132, 11, "904132"),
        ),
    ),
    2007: Config(
        pdf="Arsredovisning_2007.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(2, -4250732, 11, "4250732"),
            Item(21, -2641205, 11, "2641205"),
            Item(22, -3217666, 11, "3217666"),
            Item(23, -5230502, 11, "5230502"),
            Item(24, -1542357, 11, "1542357"),
            Item(25, -1185688, 11, "1185688"),
            Item(26, -177329, 11, "177329"),
            Item(27, -407418, 11, "407418"),
            Item(28, -397496, 11, "397496"),
            Item(29, -741853, 11, "741853"),
            Item(30, -731606, 11, "731606"),
        ),
    ),
    2006: Config(
        pdf="Arsredovisning_2006.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(2, -5252955, 9, "5252955"),
            Item(21, -3443058, 9, "3443058"),
            Item(22, -1109005, 9, "1109005"),
            Item(23, -5225467, 9, "5225467"),
            Item(24, -1684566, 9, "1684566"),
            Item(25, -1166211, 9, "1166211"),
            Item(26, -177329, 9, "177329"),
            Item(27, -382056, 9, "382056"),
            Item(28, -541977, 9, "541977"),
            Item(29, -673938, 9, "673938"),
            Item(30, -1016938, 9, "1016938"),
        ),
    ),
    2005: Config(
        pdf="bokslut2005.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(2, -4212410, 10, "4212410"),
            Item(21, -3743927, 10, "3743927"),
            Item(22, -1537077, 10, "1537077"),
            Item(23, -5400965, 10, "5400965"),
            Item(24, -1742945, 10, "1742945"),
            Item(25, -1089383, 10, "1089383"),
            Item(26, -177332, 10, "177332"),
            Item(27, -363032, 10, "363032"),
            Item(29, -637804, 10, "637804"),
            Item(30, -1209252, 10, "1209252"),
        ),
    ),
    2004: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(2, -2006203, 10, "2006203"),
            Item(21, -2235012, 10, "2235012"),
            Item(22, -1527711, 10, "1527711"),
            Item(23, -5183817, 10, "5183817"),
            Item(24, -1501285, 10, "1501285"),
            Item(25, -1073127, 10, "1073127"),
            Item(26, -304189, 10, "304189"),
            Item(27, -301369, 10, "301369"),
            Item(28, -416746, 10, "416746"),
            Item(29, -3018090, 10, "3018090"),
            Item(30, -962789, 10, "962789"),
        ),
    ),
    2003: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(2, -620471, 10, "620471"),
            Item(21, -6399854, 10, "6399854"),
            Item(22, -1427935, 10, "1427935"),
            Item(23, -4427387, 10, "4427387"),
            Item(24, -1829131, 10, "1829131"),
            Item(25, -1038802, 10, "1038802"),
            Item(26, -281122, 10, "281122"),
            Item(27, -413745, 10, "413745"),
            Item(28, -352086, 10, "352086"),
            Item(29, -4151388, 10, "4151388"),
            Item(30, -1540509, 10, "1540509"),
        ),
    ),
}


EVENT_ARSAVGIFT_CONFIGS: Dict[int, Config] = {
    2003: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(17, 32291604, 10, "32291604"),
        ),
    ),
    2004: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(17, 33266661, 10, "33266661"),
        ),
    ),
    2005: Config(
        pdf="bokslut2005.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(17, 33423511, 10, "33423511"),
        ),
    ),
    2006: Config(
        pdf="Arsredovisning_2006.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(17, 33844138, 9, "33844138"),
        ),
    ),
    2007: Config(
        pdf="Arsredovisning_2007.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(17, 34266104, 11, "34266104"),
        ),
    ),
    2008: Config(
        pdf="Arsredovisning_2008.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(17, 34263924, 11, "34263924"),
        ),
    ),
    2012: Config(
        pdf="arsredovisning_2013.pdf",
        min_left_px=1400,
        col_split_px=1900,
        source_column="previous",
        items=(
            Item(17, 32786686, 12, "32786686"),
        ),
    ),
    2011: Config(
        pdf="arsredovisning2012.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(17, 32741413, 13, "32741413"),
        ),
    ),
    2010: Config(
        pdf="arsredovisning2010.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(17, 32722676, 12, "32722676"),
        ),
    ),
    2009: Config(
        pdf="arsredovisning_2009.pdf",
        min_left_px=1500,
        col_split_px=1950,
        source_column="current",
        items=(
            Item(17, 34263924, 12, "34263924"),
        ),
    ),
}


STATE_CONFIGS: Dict[int, Config] = {
    2005: Config(
        pdf="bokslut2005.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 0, 7, "100083420"),
            Item(1, 100083420, 7, "100083420"),
            Item(2, 2634616, 6, "2634616"),
            Item(3, 2898737, 6, "2898737"),
        ),
    ),
    2008: Config(
        pdf="Arsredovisning_2008.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 0, 8, "121438258"),
            Item(1, 121438258, 8, "121438258"),
            Item(2, 12869990, 7, "12869990"),
            Item(3, 6720806, 7, "6720806"),
        ),
    ),
    2007: Config(
        pdf="Arsredovisning_2008.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(0, 0, 8, "122560522"),
            Item(1, 122560522, 8, "122560522"),
            Item(2, 7714499, 7, "7714499"),
            Item(3, 7897907, 7, "7897907"),
        ),
    ),
    2006: Config(
        pdf="Arsredovisning_2007.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(0, 0, 7, "103894036"),
            Item(1, 103894036, 7, "103894036"),
            Item(2, 1367376, 6, "1367376"),
            Item(3, 3581713, 6, "3581713"),
        ),
    ),
    2004: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="current",
        items=(
            Item(0, 0, 7, "101284430"),
            Item(1, 101284430, 7, "101284430"),
            Item(2, 3780672, 6, "3780672"),
            Item(3, 2344359, 6, "2344359"),
        ),
    ),
    2003: Config(
        pdf="Arsredovisning_2004.pdf",
        min_left_px=1200,
        col_split_px=1850,
        source_column="previous",
        items=(
            Item(0, 0, 7, "96116470"),
            Item(1, 96116470, 7, "96116470"),
            Item(2, 4506613, 6, "4506613"),
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


def _find_amount_coords(data, amount_digits: str, min_left_px: int, col_split_px: int, source_column: str):
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
        left_col = [t for t in row_tokens if t["left"] < col_split_px]
        right_col = [t for t in row_tokens if t["left"] >= col_split_px]
        preferred = left_col
        fallback = right_col
        if source_column == "previous":
            preferred = right_col
            fallback = left_col

        pref_digits = "".join(_strip_token(t["text"]) for t in preferred)
        if amount_digits in pref_digits and preferred:
            return round(preferred[0]["left"] * CANVAS_SCALE), round(preferred[0]["top"] * CANVAS_SCALE)

        fb_digits = "".join(_strip_token(t["text"]) for t in fallback)
        if amount_digits in fb_digits and fallback:
            return round(fallback[0]["left"] * CANVAS_SCALE), round(fallback[0]["top"] * CANVAS_SCALE)

        full_digits = "".join(_strip_token(t["text"]) for t in row_tokens)
        if amount_digits in full_digits:
            return round(row_tokens[0]["left"] * CANVAS_SCALE), round(row_tokens[0]["top"] * CANVAS_SCALE)

    return None


def _extract_lines(configs: Dict[int, Config], years: Iterable[int], allow_missing: bool = False) -> List[str]:
    lines: List[str] = []
    for year in years:
        cfg = configs[year]
        cache = {}
        for item in cfg.items:
            if item.page not in cache:
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
                cache[item.page] = ocr.dropna(subset=["text"])

            coords = _find_amount_coords(
                cache[item.page],
                item.amount_key,
                cfg.min_left_px,
                cfg.col_split_px,
                cfg.source_column,
            )
            if coords is None:
                if allow_missing:
                    print(
                        f"WARN: could not locate {item.amount_key} for {year} cat {item.category_id} in {cfg.pdf} page {item.page}"
                    )
                    continue
                raise RuntimeError(
                    f"Failed to locate {item.amount_key} for {year} cat {item.category_id} in {cfg.pdf} page {item.page}"
                )

            x, y = coords
            lines.append(f"{year},{item.category_id},{item.amount},{cfg.pdf},{item.page},{x},{y},{STD_W},{STD_H}")
    return lines


def _existing_keys(path: Path) -> set:
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {(int(r["year"]), int(r["category_id"])) for r in csv.DictReader(f)}


def _append_unique(path: Path, lines: List[str]) -> int:
    existing = _existing_keys(path)
    new_lines = []
    for line in lines:
        y, c, *_ = line.split(",", 2)
        key = (int(y), int(c))
        if key in existing:
            continue
        new_lines.append(line)

    if not new_lines:
        return 0

    with path.open("a", encoding="utf-8", newline="") as f:
        for line in new_lines:
            f.write(line + "\n")
    return len(new_lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract pre-2009 states/events")
    parser.add_argument("--append", action="store_true", help="Append directly to CSV files")
    args = parser.parse_args()

    years = sorted(STATE_CONFIGS.keys())
    state_lines = _extract_lines(STATE_CONFIGS, years)
    event_lines = _extract_lines(EVENT_CONFIGS, years)
    event_subcategory_lines = _extract_lines(
        EVENT_SUBCATEGORY_CONFIGS,
        sorted(EVENT_SUBCATEGORY_CONFIGS.keys()),
        allow_missing=True,
    )
    event_arsavgift_lines = _extract_lines(
        EVENT_ARSAVGIFT_CONFIGS,
        sorted(EVENT_ARSAVGIFT_CONFIGS.keys()),
    )

    if not args.append:
        print("# STATES")
        for line in state_lines:
            print(line)
        print("# EVENTS")
        for line in event_lines:
            print(line)
        print("# EVENT_SUBCATEGORIES")
        for line in event_subcategory_lines:
            print(line)
        print("# EVENT_ARSAVGIFTER")
        for line in event_arsavgift_lines:
            print(line)
        return 0

    states_path = Path("data/financial_states.csv")
    events_path = Path("data/financial_events.csv")
    added_states = _append_unique(states_path, state_lines)
    added_events = _append_unique(events_path, event_lines)
    added_event_subcategories = _append_unique(events_path, event_subcategory_lines)
    added_event_arsavgifter = _append_unique(events_path, event_arsavgift_lines)
    print(f"Appended {added_states} rows to {states_path}")
    print(f"Appended {added_events} rows to {events_path}")
    print(f"Appended {added_event_subcategories} subcategory rows to {events_path}")
    print(f"Appended {added_event_arsavgifter} arsavgift rows to {events_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
