#!/usr/bin/env python3
"""Check whether financial states contain the components needed for soliditet.

Soliditet is calculated as:

    eget_kapital / summa_tillgangar

This script validates category presence and year coverage in data/financial_states.csv.
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


def resolve_category_id(
    categories: Iterable[Dict[str, str]],
    keywords: Tuple[str, ...],
) -> Optional[str]:
    for row in categories:
        name = normalize_text(row.get("name", ""))
        if any(k in name for k in keywords):
            return (row.get("id") or "").strip()
    return None


def years_with_category(states: Iterable[Dict[str, str]], category_id: str) -> set:
    years = set()
    for row in states:
        if (row.get("category_id") or "").strip() != category_id:
            continue
        year = (row.get("year") or "").strip()
        amount = (row.get("amount") or "").strip()
        if year and amount:
            years.add(year)
    return years


def main() -> int:
    parser = argparse.ArgumentParser(description="Check soliditet readiness from financial states")
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
    args = parser.parse_args()

    if not args.states.exists():
        raise SystemExit(f"Missing states file: {args.states}")
    if not args.categories.exists():
        raise SystemExit(f"Missing categories file: {args.categories}")

    states = load_rows(args.states)
    categories = load_rows(args.categories)

    equity_id = args.equity_category_id.strip() or resolve_category_id(categories, EQUITY_KEYWORDS)
    assets_id = args.assets_category_id.strip() or resolve_category_id(categories, ASSETS_KEYWORDS)

    print(f"states_rows={len(states)}")
    print(f"categories_rows={len(categories)}")

    if not equity_id:
        print("missing_category=eget_kapital")
    else:
        print(f"equity_category_id={equity_id}")

    if not assets_id:
        print("missing_category=summa_tillgangar")
    else:
        print(f"assets_category_id={assets_id}")

    if not equity_id or not assets_id:
        print("ready=false")
        return 2

    all_years = {
        (row.get("year") or "").strip()
        for row in states
        if (row.get("year") or "").strip()
    }
    equity_years = years_with_category(states, equity_id)
    assets_years = years_with_category(states, assets_id)
    both_years = equity_years & assets_years

    missing_either = sorted(all_years - both_years, key=lambda y: int(y) if y.isdigit() else y)

    print(f"years_any={len(all_years)}")
    print(f"years_equity={len(equity_years)}")
    print(f"years_assets={len(assets_years)}")
    print(f"years_both={len(both_years)}")

    if missing_either:
        print("missing_years=" + ",".join(missing_either))
        print("ready=false")
        return 2

    print("ready=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
