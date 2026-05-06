#!/usr/bin/env python3
"""Scrape sold listings (slutpriser) from Booli for a bostadsrättsförening.

Two output modes:

* Default (per-listing): writes a per-sale CSV plus a raw JSON archive
  of the full GraphQL payload. Used for BRF Sjötungan to enable
  per-row analysis.
* `--aggregate-only`: computes annual medians of `kr_per_m2` and writes
  one aggregated CSV. No per-sale rows, no raw JSON archive. Used for
  peer-BRF comparison overlays (e.g. Björkbacken, Gäddan, Siken).

Booli scopes natively to BRF (housingCoopId), unlike Hemnet which only
filters down to street level — so no street-number range filter is
needed here. A single GraphQL call (getHousingCoopSold) returns the
BRF's complete sold-listing history in one shot: no pagination, no
50-page cap, no room sharding.

Booli is behind the same Cloudflare challenge as Hemnet, so we use
Playwright + stealth: load the BRF page once, intercept the GraphQL
response, parse it.

This endpoint does NOT expose asking price, monthly fee, or price
change %. Those live on each listing's detail page (/bostad/{id});
fetch them per-listing if you need them.
"""

import argparse
import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

BRF_URL_TPL = "https://www.booli.se/bostadsrattsforening/{id}"
GRAPHQL_OP = "getHousingCoopSold"

CSV_COLUMNS = [
    "sold_date",
    "address",
    "street_number",
    "rooms",
    "living_area_m2",
    "final_price_kr",
    "asking_price_kr",      # not exposed by getHousingCoopSold
    "kr_per_m2",
    "monthly_fee_kr",       # not exposed by getHousingCoopSold
    "price_change_pct",     # not exposed by getHousingCoopSold
    "broker_agency",
    "listing_id",
    "booli_url",
    "apartment_number",
]


# Personal data is dropped from records before anything is persisted.
# Booli's GraphQL response includes the agent's name, profile-page URL
# (often email-derived), face-photo URL, and personal ID. None are
# needed for price analysis and we have no lawful basis under GDPR to
# retain them. Only the agency (a legal entity) is kept.
PERSONAL_AGENT_FIELDS = ("agent", "agentId")


def strip_personal(records: list) -> list:
    return [{k: v for k, v in r.items() if k not in PERSONAL_AGENT_FIELDS}
            for r in records]


def parse_float_swedish(value) -> Optional[float]:
    """Parse '82,5 m²' or '3 rum' → float."""
    if value is None:
        return None
    s = str(value).replace(",", ".").replace("\xa0", " ")
    m = re.search(r"[\d.]+", s)
    return float(m.group()) if m else None


def make_street_number_extractor(street_name: str):
    """Build an extractor that pulls the integer street number after a street
    name. 'ä'/'a' are interchangeable in the match (Booli sometimes serves
    either form)."""
    flexible = re.escape(street_name).replace("ä", "[äa]").replace("Ä", "[ÄA]")
    pattern = re.compile(rf"{flexible}\s+(\d+)", re.IGNORECASE)

    def _extract(address: str) -> Optional[int]:
        m = pattern.search(address or "")
        return int(m.group(1)) if m else None

    return _extract


def fetch_brf_sold(brf_id: int, timeout_ms: int = 20000) -> dict:
    """Load the BRF page in headless Chromium and capture the
    getHousingCoopSold GraphQL response. Returns the parsed JSON
    payload (with top-level 'data' key)."""
    captured: dict = {"payload": None}

    def on_response(resp):
        if GRAPHQL_OP not in resp.url:
            return
        ctype = resp.headers.get("content-type", "")
        if "json" not in ctype:
            return
        try:
            captured["payload"] = resp.json()
        except Exception as e:
            print(f"  failed to parse {GRAPHQL_OP} response: {e}")

    with Stealth().use_sync(sync_playwright()) as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="sv-SE",
            timezone_id="Europe/Stockholm",
            viewport={"width": 1366, "height": 850},
        )
        page = ctx.new_page()
        page.on("response", on_response)

        url = BRF_URL_TPL.format(id=brf_id)
        print(f"GET {url} …")
        page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # Trigger the slutpriser section in case it's behind a hash anchor.
        try:
            page.evaluate("location.hash = '#sold'")
        except Exception:
            pass

        # Poll for the captured payload.
        waited = 0
        step = 250
        while waited < timeout_ms and captured["payload"] is None:
            page.wait_for_timeout(step)
            waited += step

        browser.close()

    if not captured["payload"]:
        raise RuntimeError(
            f"Did not capture {GRAPHQL_OP} response within {timeout_ms}ms")
    return captured["payload"]


def card_to_row(card: dict, extract_street_number) -> dict:
    """Map one Booli SoldProperty record to our CSV schema."""
    address = ((card.get("location") or {}).get("address") or {}).get("streetAddress", "") or ""
    rel_url = card.get("url") or ""
    apt = card.get("apartmentNumber") or {}
    return {
        "sold_date": card.get("soldDate"),
        "address": address,
        "street_number": extract_street_number(address),
        "rooms": (card.get("rooms") or {}).get("raw"),
        "living_area_m2": parse_float_swedish((card.get("livingArea") or {}).get("formatted")),
        "final_price_kr": (card.get("soldPrice") or {}).get("raw"),
        "asking_price_kr": None,
        "kr_per_m2": (card.get("soldSqmPrice") or {}).get("raw"),
        "monthly_fee_kr": None,
        "price_change_pct": None,
        "broker_agency": (card.get("agency") or {}).get("name"),
        "listing_id": card.get("booliId"),
        "booli_url": f"https://www.booli.se{rel_url}" if rel_url else None,
        "apartment_number": apt.get("formatted") if isinstance(apt, dict) else None,
    }


AGGREGATED_COLUMNS = [
    "year", "n", "median_kr_per_m2", "mean_kr_per_m2",
    "min_kr_per_m2", "max_kr_per_m2",
]


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


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--brf-id", type=int, default=267023,
                    help="Booli housingCoopId (default: 267023 = HSB BRF Sjötungan)")
    ap.add_argument("--street-name", default="Myggdalsvägen",
                    help="Street name used to parse the building number from each address")
    ap.add_argument("--output-csv",
                    default="data/apartment_prices/sjotungan_sales_booli.csv",
                    help="Per-listing CSV output (ignored with --aggregate-only)")
    ap.add_argument("--output-raw",
                    default="data/apartment_prices/sjotungan_sales_booli_raw.json",
                    help="Raw GraphQL payload archive (ignored with --aggregate-only)")
    ap.add_argument("--aggregate-only", action="store_true",
                    help="Write only an annual median kr/m² CSV "
                         "(no per-listing CSV, no raw JSON archive). "
                         "Used for peer-BRF comparison overlays.")
    ap.add_argument("--output-aggregated", default=None,
                    help="Annual-medians CSV path (required with --aggregate-only).")
    args = ap.parse_args()

    payload = fetch_brf_sold(args.brf_id)
    sold = (payload.get("data") or {}).get("sold") or []
    print(f"Captured {len(sold)} sold listings for BRF {args.brf_id}")

    sold = strip_personal(sold)
    payload["data"]["sold"] = sold

    extract_street_number = make_street_number_extractor(args.street_name)
    rows = [card_to_row(c, extract_street_number) for c in sold]

    if args.aggregate_only:
        if not args.output_aggregated:
            raise SystemExit("--aggregate-only requires --output-aggregated.")
        annual = aggregate_annual_medians(rows)
        out_path = Path(args.output_aggregated)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=AGGREGATED_COLUMNS)
            w.writeheader()
            w.writerows(annual)
        print(f"Aggregated CSV written to {out_path} ({len(annual)} years)")
        return

    raw_path = Path(args.output_raw)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"Raw JSON written to {raw_path}")

    rows.sort(key=lambda r: r["sold_date"] or "", reverse=True)
    with open(args.output_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"CSV written to {args.output_csv} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
