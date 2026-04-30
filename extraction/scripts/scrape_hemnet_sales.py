#!/usr/bin/env python3
"""
Scrape sold listings (slutpriser) from Hemnet for a given street.

Two output modes:

* Default (per-listing): writes a per-sale CSV plus a raw JSON archive of
  all SaleCard objects. Used for BRF Sjötungan to enable per-row analysis.
* `--aggregate-only`: computes annual medians of `kr_per_m2` and writes a
  single aggregated CSV. No per-sale rows, no raw JSON archive. Used for
  reference streets where we only care about the time series (e.g.
  Sikvägen, Tyresö).

Examples
--------
# BRF Sjötungan, per-listing (defaults)
python extraction/scripts/scrape_hemnet_sales.py

# Sikvägen, Tyresö — annual medians only
python extraction/scripts/scrape_hemnet_sales.py \\
    --location-id 485023 --street-name Sikvägen \\
    --aggregate-only \\
    --output-aggregated data/apartment_prices/sikvagen_annual_medians.csv

# Re-parse the per-listing CSV from the cached raw JSON without re-fetching
python extraction/scripts/scrape_hemnet_sales.py --from-cache
"""

import argparse
import csv
import json
import os
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

import requests

BASE_URL = "https://www.hemnet.se/salda/bostader"
ITEM_TYPE = "bostadsratt"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

CSV_COLUMNS = [
    "sold_date",
    "address",
    "street_number",
    "rooms",
    "living_area_m2",
    "final_price_kr",
    "asking_price_kr",
    "kr_per_m2",
    "monthly_fee_kr",
    "price_change_pct",
    "broker_agency",
    "broker_name",
    "listing_id",
    "hemnet_url",
]


def _build_query_url(page: int, location_id: int,
                     extra_params: Optional[dict] = None) -> str:
    """Compose the slutpriser query URL — shared by requests + Playwright fetchers."""
    from urllib.parse import urlencode
    items = [
        ("location_ids[]", str(location_id)),
        ("item_types[]", ITEM_TYPE),
        ("by", "sold_at"),
        ("order", "desc"),
        ("page", str(page)),
    ]
    if extra_params:
        for k, v in extra_params.items():
            items.append((k, str(v)))
    return f"{BASE_URL}?{urlencode(items)}"


def fetch_page(session, page: int, location_id: int,
               extra_params: Optional[dict] = None) -> str:
    """Fetch one slutpriser page. Dispatches on session type:
    - requests.Session → plain HTTP (fast, but blocked when Hemnet enables
      Cloudflare's challenge mitigation).
    - PlaywrightSession (see below) → headless Chromium with stealth tweaks,
      which earns a cf_clearance cookie and proxies all subsequent fetches
      through it. ~10× slower per page but works through Cloudflare.
    """
    if isinstance(session, PlaywrightSession):
        return session.fetch(_build_query_url(page, location_id, extra_params))
    params = {
        "location_ids[]": location_id,
        "item_types[]": ITEM_TYPE,
        "by": "sold_at",
        "order": "desc",
        "page": page,
    }
    if extra_params:
        params.update(extra_params)
    resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


class PlaywrightSession:
    """Headless-Chromium session with stealth, used as a drop-in for
    requests.Session when Hemnet's Cloudflare challenge is active. The
    browser/context are reused across all fetches so the cf_clearance
    cookie persists for the lifetime of one scrape."""

    def __init__(self):
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth
        self._stealth_ctx = Stealth().use_sync(sync_playwright())
        self._pw = self._stealth_ctx.__enter__()
        self._browser = self._pw.chromium.launch(headless=True)
        self._context = self._browser.new_context(
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
            viewport={"width": 1366, "height": 850},
        )
        self._page = self._context.new_page()

    def fetch(self, url: str) -> str:
        resp = self._page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if resp is None:
            raise RuntimeError(f"Playwright returned no response for {url}")
        # __NEXT_DATA__ is a <script> tag (not "visible"); wait for it attached.
        self._page.wait_for_selector('script#__NEXT_DATA__', state="attached", timeout=30000)
        return self._page.content()

    def close(self):
        try:
            self._browser.close()
        finally:
            self._stealth_ctx.__exit__(None, None, None)


def extract_next_data(html: str) -> dict:
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not m:
        raise ValueError("__NEXT_DATA__ script tag not found in page HTML")
    return json.loads(m.group(1))


def collect_sale_cards(obj, cards: list):
    """Recursively walk JSON and collect objects where __typename == 'SaleCard'."""
    if isinstance(obj, dict):
        if obj.get("__typename") == "SaleCard":
            cards.append(obj)
        else:
            for v in obj.values():
                collect_sale_cards(v, cards)
    elif isinstance(obj, list):
        for item in obj:
            collect_sale_cards(item, cards)


def parse_int_kr(value) -> Optional[int]:
    if value is None:
        return None
    s = re.sub(r"[^\d]", "", str(value))
    return int(s) if s else None


def parse_float_swedish(value) -> Optional[float]:
    """Parse strings like '75,5 m²' or '3 rum' to float."""
    if value is None:
        return None
    s = str(value).replace(",", ".").replace("\xa0", " ")
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


def parse_price_change_pct(value) -> Optional[float]:
    """Parse '+5,2 %' → 0.052, '-3,1 %' → -0.031."""
    if value is None:
        return None
    s = str(value).replace(",", ".").replace("\xa0", " ").replace(" ", "")
    m = re.search(r"([+-]?[\d.]+)%", s)
    if not m:
        return None
    return float(m.group(1)) / 100


def parse_sold_date(timestamp) -> Optional[str]:
    if timestamp is None:
        return None
    try:
        dt = datetime.fromtimestamp(int(float(timestamp)), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return str(timestamp)


def make_street_number_extractor(street_name: str):
    """Build an extractor that pulls the integer number after a given street name.

    The street name is matched case-insensitively, with 'ä'/'a' treated as
    interchangeable (Hemnet sometimes serves either form).
    """
    flexible = re.escape(street_name).replace("ä", "[äa]").replace("Ä", "[ÄA]")
    pattern = re.compile(rf"{flexible}\s+(\d+)", re.IGNORECASE)

    def _extract(address: str) -> Optional[int]:
        m = pattern.search(address or "")
        return int(m.group(1)) if m else None

    return _extract


def card_to_row(card: dict, extract_street_number) -> dict:
    address = card.get("streetAddress", "")
    street_number = extract_street_number(address)
    slug = card.get("slug", "")
    return {
        "sold_date": parse_sold_date(card.get("soldAt")),
        "address": address,
        "street_number": street_number,
        "rooms": parse_float_swedish(card.get("rooms")),
        "living_area_m2": parse_float_swedish(card.get("livingArea")),
        "final_price_kr": parse_int_kr(card.get("finalPrice")),
        "asking_price_kr": parse_int_kr(card.get("askingPrice")),
        "kr_per_m2": parse_int_kr(card.get("squareMeterPrice")),
        "monthly_fee_kr": parse_int_kr(card.get("fee")),
        "price_change_pct": parse_price_change_pct(card.get("priceChange")),
        "broker_agency": card.get("brokerAgencyName"),
        "broker_name": card.get("brokerName"),
        "listing_id": card.get("listingId"),
        "hemnet_url": f"https://www.hemnet.se/salda/{slug}" if slug else None,
    }


def scrape_one_shard(session: requests.Session, location_id: int,
                     extra_params: Optional[dict], seen_ids: set,
                     shard_label: str = "") -> list:
    """Paginate one query (location + optional filters) and return new cards.

    `seen_ids` is mutated in place — listings already collected by an earlier
    shard are skipped silently. The function keeps paginating until either
    Hemnet stops returning cards or every card on a page is already seen
    (which means the cap was reached and we're seeing recycled stale rows).
    """
    new_cards: list = []
    page = 1
    label = f" [{shard_label}]" if shard_label else ""
    while True:
        print(f"  Fetching page {page}{label}…", end=" ", flush=True)
        html = fetch_page(session, page, location_id, extra_params)
        try:
            next_data = extract_next_data(html)
        except ValueError as e:
            print(f"\nERROR: {e}")
            break

        cards: list = []
        collect_sale_cards(next_data, cards)
        print(f"{len(cards)} SaleCards found")

        if not cards:
            print(f"  No more listings on page {page}{label} — moving on.")
            break

        page_new = 0
        for card in cards:
            lid = card.get("listingId")
            if lid and lid in seen_ids:
                continue
            if lid:
                seen_ids.add(lid)
            new_cards.append(card)
            page_new += 1

        # NB: don't break on page_new == 0. When sharding, a shard's first
        # pages can be entirely covered by the baseline shard, but later
        # pages still reach older years not yet seen. Only stop when Hemnet
        # returns no SaleCards (handled above) or we hit the 50-page cap.
        if page >= 50:
            print(f"  Reached Hemnet's 50-page cap on shard{label} — moving on.")
            break

        page += 1
        time.sleep(1.2)  # polite crawl delay

    return new_cards


# Room-count shards: (rooms_min, rooms_max). 6+ uses no upper bound. The
# leading None shard runs an unfiltered baseline so listings whose rooms
# field is null or fractional (e.g. "3,5 rum") aren't dropped by the
# integer-only room filter — Hemnet's rooms_min/max filter is exact-integer
# match. Costs one extra ~50-page sweep but ensures recent years are complete.
ROOM_SHARDS = [
    (None, "unfiltered"),
    ({"rooms_min": 1, "rooms_max": 1}, "1 rum"),
    ({"rooms_min": 2, "rooms_max": 2}, "2 rum"),
    ({"rooms_min": 3, "rooms_max": 3}, "3 rum"),
    ({"rooms_min": 4, "rooms_max": 4}, "4 rum"),
    ({"rooms_min": 5, "rooms_max": 5}, "5 rum"),
    ({"rooms_min": 6}, "6+ rum"),
]


def scrape_all(location_id: int, shard_by_rooms: bool = False,
               use_playwright: bool = False) -> list:
    session = PlaywrightSession() if use_playwright else requests.Session()
    seen_ids: set = set()
    all_raw_cards: list = []

    try:
        if not shard_by_rooms:
            return scrape_one_shard(session, location_id, None, seen_ids)

        print("Sharding by room count to expand depth past Hemnet's 2,500-result cap.")
        for params, label in ROOM_SHARDS:
            cards = scrape_one_shard(session, location_id, params, seen_ids, label)
            all_raw_cards.extend(cards)
            print(f"  Shard {label}: +{len(cards)} new (total unique so far: {len(seen_ids)})\n")
        return all_raw_cards
    finally:
        if isinstance(session, PlaywrightSession):
            session.close()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--location-id", type=int, default=485045,
                   help="Hemnet street-level location_id (default: 485045 = Myggdalsvägen, Tyresö)")
    p.add_argument("--street-name", default="Myggdalsvägen",
                   help="Street name used to parse the building number from each address (default: Myggdalsvägen)")
    p.add_argument("--number-min", type=int, default=6,
                   help="Lowest building number to keep (default: 6). Pass alongside --number-max.")
    p.add_argument("--number-max", type=int, default=122,
                   help="Highest building number to keep (default: 122). Pass alongside --number-min.")
    p.add_argument("--number-set", default=None,
                   help="Comma-separated explicit allow-list of building numbers, e.g. '29,31,33,38,40'. "
                        "Use for BRFs whose addresses don't form a contiguous range. Overrides "
                        "--number-min/--number-max when provided.")
    p.add_argument("--no-filter", action="store_true",
                   help="Disable number filtering — keep every listing on the street.")
    p.add_argument("--output-csv", default="data/apartment_prices/sjotungan_sales.csv",
                   help="Path to the filtered CSV output (default: data/apartment_prices/sjotungan_sales.csv)")
    p.add_argument("--output-raw", default="data/apartment_prices/sjotungan_sales_raw.json",
                   help="Path to the raw JSON archive of all SaleCards (default: data/apartment_prices/sjotungan_sales_raw.json)")
    p.add_argument("--from-cache", action="store_true",
                   help="Re-parse the CSV from the existing raw JSON without re-fetching Hemnet.")
    p.add_argument("--aggregate-only", action="store_true",
                   help="Write only an annual median kr/m² CSV (no per-listing CSV, no raw JSON).")
    p.add_argument("--output-aggregated", default=None,
                   help="Path to the aggregated annual-medians CSV (used with --aggregate-only).")
    p.add_argument("--shard-by-rooms", action="store_true",
                   help="Run six separate queries (rooms 1, 2, 3, 4, 5, 6+) and union/dedup. "
                        "Useful for broad locations where a single query would hit Hemnet's "
                        "2,500-result cap. Multiplies request count ~6x.")
    p.add_argument("--use-playwright", action="store_true",
                   help="Fetch via headless Chromium (Playwright + stealth) instead of requests. "
                        "Required when Hemnet has Cloudflare's challenge mitigation enabled — "
                        "plain HTTP returns 403 in that mode. ~10× slower per page; reuses one "
                        "browser context so cf_clearance is solved once and reused.")
    p.add_argument("--source", action="append", default=None,
                   help="Multi-source spec 'LOCATION_ID:STREET_NAME:NUMBER_SET' (repeatable). "
                        "Use for BRFs that span multiple streets — e.g. BRF Björkbacken which "
                        "covers part of Björkbacksvägen and part of Bollmoravägen. Each source "
                        "is fetched separately with its own location_id, parsed with its own "
                        "street-name regex, and filtered to its own number set; the unioned "
                        "rows are then aggregated. Implies --aggregate-only and ignores the "
                        "single-source --location-id / --street-name / --number-* args.")
    return p.parse_args()


AGGREGATED_COLUMNS = ["year", "n", "median_kr_per_m2", "mean_kr_per_m2", "min_kr_per_m2", "max_kr_per_m2"]


def aggregate_annual_medians(rows: list) -> list:
    by_year = defaultdict(list)
    for r in rows:
        if r["sold_date"] and r["kr_per_m2"]:
            by_year[r["sold_date"][:4]].append(int(r["kr_per_m2"]))
    out = []
    for year in sorted(by_year):
        vals = by_year[year]
        out.append({
            "year": year,
            "n": len(vals),
            "median_kr_per_m2": int(statistics.median(vals)),
            "mean_kr_per_m2": round(statistics.mean(vals)),
            "min_kr_per_m2": min(vals),
            "max_kr_per_m2": max(vals),
        })
    return out


def parse_source_spec(spec: str) -> tuple:
    """Parse 'LOCATION_ID:STREET_NAME:NUMBER_SET' into (loc_id, street, allowed-set)."""
    parts = spec.split(":", 2)
    if len(parts) != 3:
        raise SystemExit(f"--source must be 'LOCATION_ID:STREET_NAME:NUMBER_SET'; got {spec!r}")
    loc_str, street, numset = parts
    try:
        loc_id = int(loc_str)
    except ValueError:
        raise SystemExit(f"--source location_id must be an integer; got {loc_str!r}")
    try:
        allowed = {int(x) for x in numset.split(",") if x.strip()}
    except ValueError as e:
        raise SystemExit(f"--source number_set must be comma-separated integers: {e}")
    if not allowed:
        raise SystemExit(f"--source number_set is empty in {spec!r}")
    return loc_id, street, allowed


def run_multi_source(args) -> None:
    """Fetch each --source separately, filter each by its own number set, union
    the rows, and write the aggregated annual-medians CSV."""
    if args.from_cache:
        raise SystemExit("--source is incompatible with --from-cache (no per-source raw cache).")
    if not args.aggregate_only:
        raise SystemExit("--source currently requires --aggregate-only.")
    if not args.output_aggregated:
        raise SystemExit("--source requires --output-aggregated.")

    sources = [parse_source_spec(s) for s in args.source]
    print(f"Multi-source mode: {len(sources)} sources")

    session = PlaywrightSession() if args.use_playwright else requests.Session()
    combined: list = []
    try:
        for loc_id, street, allowed in sources:
            print(f"\n=== Source: {street} (location_id={loc_id}, "
                  f"{len(allowed)} numbers in scope) ===")
            extractor = make_street_number_extractor(street)
            seen_ids: set = set()
            raw: list = []
            if args.shard_by_rooms:
                for params, label in ROOM_SHARDS:
                    raw.extend(scrape_one_shard(session, loc_id, params, seen_ids, label))
            else:
                raw.extend(scrape_one_shard(session, loc_id, None, seen_ids))
            rows = [card_to_row(c, extractor) for c in raw]
            in_scope = [r for r in rows
                        if r["street_number"] is not None and r["street_number"] in allowed]
            print(f"  {street}: {len(raw)} raw cards → {len(in_scope)} in-scope listings")
            combined.extend(in_scope)
    finally:
        if isinstance(session, PlaywrightSession):
            session.close()

    print(f"\nTotal in-scope listings across sources: {len(combined)}")
    annual = aggregate_annual_medians(combined)
    with open(args.output_aggregated, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=AGGREGATED_COLUMNS)
        writer.writeheader()
        writer.writerows(annual)
    print(f"Aggregated CSV written to {args.output_aggregated} ({len(annual)} years)")


def main():
    args = parse_args()

    if args.source:
        run_multi_source(args)
        return

    extract_street_number = make_street_number_extractor(args.street_name)

    if args.from_cache:
        if args.aggregate_only:
            raise SystemExit("--from-cache is incompatible with --aggregate-only (no raw JSON is persisted in aggregate mode).")
        print(f"Re-parsing from cached {args.output_raw}…")
        with open(args.output_raw, encoding="utf-8") as f:
            raw_cards = json.load(f)
    else:
        print(f"Scraping Hemnet slutpriser for {args.street_name} (location_id={args.location_id})…")
        raw_cards = scrape_all(args.location_id, shard_by_rooms=args.shard_by_rooms,
                               use_playwright=args.use_playwright)

    print(f"\nTotal raw SaleCards collected: {len(raw_cards)}")

    all_rows = [card_to_row(c, extract_street_number) for c in raw_cards]

    if args.no_filter:
        filtered = [r for r in all_rows if r["street_number"] is not None]
        scope_label = f"all parseable {args.street_name} N"
    elif args.number_set:
        try:
            allowed = {int(x) for x in args.number_set.split(",") if x.strip()}
        except ValueError as e:
            raise SystemExit(f"--number-set must be comma-separated integers: {e}")
        filtered = [
            r for r in all_rows
            if r["street_number"] is not None and r["street_number"] in allowed
        ]
        scope_label = f"set of {len(allowed)} numbers"
    else:
        filtered = [
            r for r in all_rows
            if r["street_number"] is not None
            and args.number_min <= r["street_number"] <= args.number_max
        ]
        scope_label = f"#{args.number_min}–#{args.number_max}"
    print(f"Listings in scope ({scope_label}): {len(filtered)}")

    if args.aggregate_only:
        out_path = args.output_aggregated
        if not out_path:
            raise SystemExit("--aggregate-only requires --output-aggregated.")
        annual = aggregate_annual_medians(filtered)
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=AGGREGATED_COLUMNS)
            writer.writeheader()
            writer.writerows(annual)
        print(f"Aggregated CSV written to {out_path} ({len(annual)} years)")
        return

    with open(args.output_raw, "w", encoding="utf-8") as f:
        json.dump(raw_cards, f, ensure_ascii=False, indent=2)
    print(f"Raw JSON written to {args.output_raw}")

    filtered.sort(key=lambda r: r["sold_date"] or "", reverse=True)
    with open(args.output_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(filtered)
    print(f"CSV written to {args.output_csv} ({len(filtered)} rows)")


if __name__ == "__main__":
    main()
