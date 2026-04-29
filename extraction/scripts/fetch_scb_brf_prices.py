#!/usr/bin/env python3
"""
Fetch annual sold-tenant-owned-flats prices for Sweden from SCB.

Source: SCB PxWeb, table BO/BO0501/BO0501C/FastprisBRFRegionAr
URL:    https://api.scb.se/OV0104/v1/doris/en/ssd/BO/BO0501/BO0501C/FastprisBRFRegionAr

Returns annual aggregates by region:
  - Number of sold tenant-owned flats
  - Average price (SEK thousands)
  - Median price (SEK thousands)

Note: SCB does not publish kr/m² for BRFs as a public time series — only
total prices. The output of this script is therefore in SEK thousands
and is not directly unit-comparable to the per-m² Hemnet medians.

Default output: data/apartment_prices/sweden_brf_annual.csv (region = "00", whole country).
"""

import argparse
import csv
import json
import urllib.request

SCB_URL = "https://api.scb.se/OV0104/v1/doris/en/ssd/BO/BO0501/BO0501C/FastprisBRFRegionAr"
DEFAULT_REGION = "00"  # 00 = Sweden


def fetch_region(region: str) -> list:
    query = {
        "query": [
            {"code": "Region", "selection": {"filter": "item", "values": [region]}},
            {"code": "ContentsCode",
             "selection": {"filter": "item",
                           "values": ["BO0501R6", "BO0501R7", "BO0501R8"]}},
        ],
        "response": {"format": "json"},
    }
    req = urllib.request.Request(
        SCB_URL,
        data=json.dumps(query).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def to_rows(scb_payload: dict) -> list:
    out = []
    for row in scb_payload["data"]:
        region, year = row["key"]
        n, avg_kkr, med_kkr = row["values"]
        out.append({
            "year": year,
            "region": region,
            "n_sold": int(n) if n != ".." else None,
            "avg_price_kkr": int(avg_kkr) if avg_kkr != ".." else None,
            "median_price_kkr": int(med_kkr) if med_kkr != ".." else None,
        })
    return out


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default=DEFAULT_REGION,
                   help='SCB region code (default "00" = Sweden). Examples: "01" Stockholm county, "0010" Greater Stockholm.')
    p.add_argument("--output", default="data/apartment_prices/sweden_brf_annual.csv",
                   help="Path to the output CSV (default: data/apartment_prices/sweden_brf_annual.csv).")
    return p.parse_args()


def main():
    args = parse_args()
    print(f"Fetching SCB FastprisBRFRegionAr for region={args.region}…")
    payload = fetch_region(args.region)
    rows = to_rows(payload)
    rows.sort(key=lambda r: r["year"])
    cols = ["year", "region", "n_sold", "avg_price_kkr", "median_price_kkr"]
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.output}  ({rows[0]['year']}–{rows[-1]['year']})")


if __name__ == "__main__":
    main()
