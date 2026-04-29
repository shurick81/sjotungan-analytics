#!/usr/bin/env python3
"""
Scrape sold listings (slutpriser) from Hemnet for BRF Sjötungan.
Street: Myggdalsvägen, Tyresö (location_id=485045)
Keeps only listings with street numbers 6–122 inclusive.
Output: data/sjotungan_sales.csv and data/sjotungan_sales_raw.json
"""

import csv
import json
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests

BASE_URL = "https://www.hemnet.se/salda/bostader"
LOCATION_ID = 485045
ITEM_TYPE = "bostadsratt"
MIN_NUMBER = 6
MAX_NUMBER = 122
OUTPUT_CSV = "data/sjotungan_sales.csv"
OUTPUT_RAW_JSON = "data/sjotungan_sales_raw.json"

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


def fetch_page(session: requests.Session, page: int) -> str:
    params = {
        "location_ids[]": LOCATION_ID,
        "item_types[]": ITEM_TYPE,
        "by": "sold_at",
        "order": "desc",
        "page": page,
    }
    resp = session.get(BASE_URL, params=params, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


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


def extract_street_number(address: str) -> Optional[int]:
    m = re.search(r"Myggdalsv[äa]gen\s+(\d+)", address, re.IGNORECASE)
    return int(m.group(1)) if m else None


def card_to_row(card: dict) -> dict:
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


def scrape_all() -> tuple[list[dict], list[dict]]:
    session = requests.Session()
    seen_ids: set = set()
    all_raw_cards: list[dict] = []
    all_rows: list[dict] = []

    page = 1
    while True:
        print(f"  Fetching page {page}…", end=" ", flush=True)
        html = fetch_page(session, page)
        try:
            next_data = extract_next_data(html)
        except ValueError as e:
            print(f"\nERROR: {e}")
            break

        cards: list[dict] = []
        collect_sale_cards(next_data, cards)
        print(f"{len(cards)} SaleCards found")

        new_count = 0
        for card in cards:
            lid = card.get("listingId")
            if lid and lid in seen_ids:
                continue
            if lid:
                seen_ids.add(lid)
            all_raw_cards.append(card)
            new_count += 1

        if new_count == 0:
            print(f"  No new listings on page {page} — stopping.")
            break

        all_rows.extend(card_to_row(c) for c in cards if c.get("listingId") not in seen_ids - {c.get("listingId")})

        page += 1
        time.sleep(1.2)  # polite crawl delay

    # Re-derive rows cleanly from deduplicated raw cards
    all_rows = [card_to_row(c) for c in all_raw_cards]
    return all_rows, all_raw_cards


def reparse_from_cache() -> tuple[list[dict], list[dict]]:
    """Re-parse CSV from existing raw JSON without re-fetching Hemnet."""
    with open(OUTPUT_RAW_JSON, encoding="utf-8") as f:
        raw_cards = json.load(f)
    rows = [card_to_row(c) for c in raw_cards]
    return rows, raw_cards


def main():
    import sys
    from_cache = "--from-cache" in sys.argv

    if from_cache:
        print(f"Re-parsing from cached {OUTPUT_RAW_JSON}…")
        all_rows, raw_cards = reparse_from_cache()
    else:
        print("Scraping Hemnet slutpriser for Myggdalsvägen, Tyresö…")
        all_rows, raw_cards = scrape_all()

    print(f"\nTotal raw SaleCards collected: {len(raw_cards)}")

    # Save raw JSON (unfiltered)
    with open(OUTPUT_RAW_JSON, "w", encoding="utf-8") as f:
        json.dump(raw_cards, f, ensure_ascii=False, indent=2)
    print(f"Raw JSON written to {OUTPUT_RAW_JSON}")

    # Filter to BRF Sjötungan address range
    filtered = [
        r for r in all_rows
        if r["street_number"] is not None
        and MIN_NUMBER <= r["street_number"] <= MAX_NUMBER
    ]
    print(f"Listings after filtering to #{MIN_NUMBER}–#{MAX_NUMBER}: {len(filtered)}")

    # Sort by sold_date descending
    filtered.sort(key=lambda r: r["sold_date"] or "", reverse=True)

    # Write CSV with UTF-8 BOM
    with open(OUTPUT_CSV, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(filtered)
    print(f"CSV written to {OUTPUT_CSV} ({len(filtered)} rows)")


if __name__ == "__main__":
    main()
