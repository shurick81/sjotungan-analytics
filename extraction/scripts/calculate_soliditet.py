#!/usr/bin/env python3
"""Calculate soliditet by year from financial states.

Formula:

    soliditet = eget_kapital / summa_tillgangar

By default, the script tries to resolve category IDs from state_categories.csv.
Use --equity-category-id and --assets-category-id to pin exact IDs.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_STATES_PATH = Path("data/financial_states.csv")
DEFAULT_CATEGORIES_PATH = Path("data/state_categories.csv")

EQUITY_KEYWORDS = (
    "eget kapital",
    "summa eget kapital",
)
ASSETS_KEYWORDS = (
    "summa tillgangar",
    "summa tillgångar",
    "balansomslutning",
)


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def normalize_text(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def parse_amount(raw: str) -> int:
    cleaned = (
        (raw or "")
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace(",", "")
        .strip()
    )
    return int(cleaned)


def resolve_category_id(
    categories: Iterable[Dict[str, str]],
    keywords: Tuple[str, ...],
) -> Optional[str]:
    for row in categories:
        name = normalize_text(row.get("name", ""))
        if any(k in name for k in keywords):
            return (row.get("id") or "").strip()
    return None


def collect_category_by_year(states: Iterable[Dict[str, str]], category_id: str) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for row in states:
        if (row.get("category_id") or "").strip() != category_id:
            continue
        year = (row.get("year") or "").strip()
        amount = (row.get("amount") or "").strip()
        if not year or not amount:
            continue
        out[year] = parse_amount(amount)
    return out


def write_csv(rows: List[Dict[str, str]], output: Optional[Path]) -> None:
    fieldnames = ["year", "eget_kapital", "summa_tillgangar", "soliditet"]
    if output:
        with output.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return

    writer = csv.DictWriter(__import__("sys").stdout, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Calculate soliditet from financial states")
    parser.add_argument(
        "--states",
        type=Path,
        default=DEFAULT_STATES_PATH,
        help="Path to financial_states.csv",
    )
    parser.add_argument(
        "--categories",
        type=Path,
        default=DEFAULT_CATEGORIES_PATH,
        help="Path to state_categories.csv",
    )
    parser.add_argument(
        "--equity-category-id",
        default="",
        help="Explicit category id for eget kapital",
    )
    parser.add_argument(
        "--assets-category-id",
        default="",
        help="Explicit category id for summa tillgangar",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write output CSV to this path (default: stdout)",
    )
    args = parser.parse_args()

    if not args.states.exists():
        raise SystemExit(f"Missing states file: {args.states}")
    if not args.categories.exists():
        raise SystemExit(f"Missing categories file: {args.categories}")

    states = load_rows(args.states)
    categories = load_rows(args.categories)

    equity_id = args.equity_category_id.strip() or resolve_category_id(categories, EQUITY_KEYWORDS)
    assets_id = args.assets_category_id.strip() or resolve_category_id(categories, ASSETS_KEYWORDS)

    if not equity_id:
        raise SystemExit("Could not resolve category for eget kapital. Use --equity-category-id.")
    if not assets_id:
        raise SystemExit("Could not resolve category for summa tillgangar. Use --assets-category-id.")

    equity = collect_category_by_year(states, equity_id)
    assets = collect_category_by_year(states, assets_id)

    years = sorted(set(equity.keys()) & set(assets.keys()), key=lambda y: int(y) if y.isdigit() else y)
    rows: List[Dict[str, str]] = []

    for year in years:
        ek = equity[year]
        sa = assets[year]
        if sa == 0:
            continue
        soliditet = ek / sa
        rows.append(
            {
                "year": year,
                "eget_kapital": str(ek),
                "summa_tillgangar": str(sa),
                "soliditet": f"{soliditet:.6f}",
            }
        )

    write_csv(rows, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
