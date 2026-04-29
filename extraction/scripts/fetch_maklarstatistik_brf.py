#!/usr/bin/env python3
"""
Fetch BRF (bostadsrätter) median kr/m² aggregates from Mäklarstatistik.

Source:
  https://www.maklarstatistik.se/omrade/<area-path>/

Mäklarstatistik publishes the underlying chart data via a WordPress
admin-ajax endpoint:

  POST https://www.maklarstatistik.se/wp-admin/admin-ajax.php
       action=price_trends_comparison
       post_ids[]=<post_id>
       housing_tenure=bostadsratter

The response payload includes:
  - `chart_data["year-by-year"][0]["data"]` — annual median kr/m²
    (one value per year; labels in `year_by_year_labels`)
  - `chart_data["12-months"][0]["data"]` — rolling 12-month median
    kr/m² for the most recent 12 months. Each value is the median
    over the 12 months ending at that label (from data-labels in the
    page HTML, e.g. "apr -25" .. "mar -26").

This bypasses Hemnet's 2,500-result cap and gives us a national /
regional time series that already includes the most recent month
(typically a one-month lag). Yearly history goes back to 1996.

Outputs (one CSV per series):
  data/apartment_prices/maklarstatistik_<region>_brf_annual.csv
    year, kr_per_m2

  data/apartment_prices/maklarstatistik_<region>_brf_rolling12.csv
    period_label, period_end_year, period_end_month, kr_per_m2

Currently configured regions:
  - sverige        post_id=6922  (https://www.maklarstatistik.se/omrade/riket/)
  - stockholms_lan post_id=5948  (.../riket/stockholms-lan/)

Re-run periodically to refresh — Mäklarstatistik updates monthly.
"""

import argparse
import csv
import html
import json
import re
import sys
import urllib.parse
import urllib.request
from typing import Optional

AJAX_URL = "https://www.maklarstatistik.se/wp-admin/admin-ajax.php"
PAGE_URL_TEMPLATE = "https://www.maklarstatistik.se/omrade/{path}/"

REGIONS = {
    "sverige":        {"post_id": 6922, "page_path": "riket"},
    "stockholms_lan": {"post_id": 5948, "page_path": "riket/stockholms-lan"},
}

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120 Safari/537.36")

SWEDISH_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "maj": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dec": 12,
}


def fetch_chart_data(post_id: int) -> dict:
    body = urllib.parse.urlencode([
        ("action", "price_trends_comparison"),
        ("post_ids[]", str(post_id)),
        ("housing_tenure", "bostadsratter"),
    ]).encode()
    req = urllib.request.Request(
        AJAX_URL, data=body,
        headers={
            "User-Agent": UA,
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded",
        })
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if not payload.get("success"):
        raise RuntimeError(f"Mäklarstatistik AJAX failed: {payload}")
    return payload["data"]


def fetch_monthly_labels(page_path: str) -> Optional[list]:
    """Pull the 12 monthly labels (e.g. 'apr -25') from the data-labels
    attribute of the rendered chart on the region page. The AJAX payload
    omits these for the 12-month series — the page renders them server-side."""
    url = PAGE_URL_TEMPLATE.format(path=page_path)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        page = r.read().decode("utf-8", errors="replace")
    # Anchor the search to the BRF block and the first 12m chart inside it
    # (br-12m-prisutveckling). data-labels values are HTML-encoded.
    m = re.search(
        r'data-chart="br-12m-prisutveckling".*?data-labels="([^"]+)"',
        page, re.DOTALL)
    if not m:
        return None
    raw = html.unescape(m.group(1))
    return json.loads(raw)


def parse_label(label: str) -> tuple:
    """'apr -25' -> (2025, 4). Year window is 2-digit, +2000."""
    parts = label.strip().split()
    if len(parts) != 2:
        raise ValueError(f"unexpected label format: {label!r}")
    mon = SWEDISH_MONTHS.get(parts[0].lower())
    if mon is None:
        raise ValueError(f"unknown Swedish month: {parts[0]!r}")
    yy = int(parts[1].lstrip("-"))
    return (2000 + yy, mon)


def write_annual(data: dict, out_path: str) -> int:
    labels = data["year_by_year_labels"]
    series = data["chart_data"]["year-by-year"][0]["data"]
    if len(labels) != len(series):
        raise RuntimeError(
            f"label/series length mismatch: {len(labels)} vs {len(series)}")
    rows = []
    for label, point in zip(labels, series):
        rows.append({"year": int(label),
                     "kr_per_m2": int(round(float(point["value"])))})
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["year", "kr_per_m2"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def write_rolling12(data: dict, labels: list, out_path: str) -> int:
    series = data["chart_data"]["12-months"][0]["data"]
    if len(labels) != len(series):
        raise RuntimeError(
            f"label/series length mismatch: {len(labels)} vs {len(series)}")
    rows = []
    for label, point in zip(labels, series):
        y, m = parse_label(label)
        rows.append({
            "period_label": label,
            "period_end_year": y,
            "period_end_month": m,
            "kr_per_m2": int(round(float(point["value"]))),
        })
    cols = ["period_label", "period_end_year", "period_end_month", "kr_per_m2"]
    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--region", default=None,
                   help="Named region key (default: all configured regions). "
                        f"Available: {', '.join(REGIONS)}")
    p.add_argument("--out-dir", default="data/apartment_prices",
                   help="Directory for output CSVs (default: data/apartment_prices).")
    return p.parse_args()


def run_one(name: str, cfg: dict, out_dir: str) -> None:
    print(f"\n[{name}] post_id={cfg['post_id']} page={cfg['page_path']}")
    data = fetch_chart_data(cfg["post_id"])
    labels = fetch_monthly_labels(cfg["page_path"])
    if labels is None:
        print(f"  WARN: monthly labels not found on page; skipping rolling12.",
              file=sys.stderr)

    annual_path = f"{out_dir}/maklarstatistik_{name}_brf_annual.csv"
    n = write_annual(data, annual_path)
    print(f"  Annual:    {n} rows -> {annual_path}")

    if labels is not None:
        rolling_path = f"{out_dir}/maklarstatistik_{name}_brf_rolling12.csv"
        n = write_rolling12(data, labels, rolling_path)
        print(f"  Rolling12: {n} rows -> {rolling_path}")


def main():
    args = parse_args()
    targets = [args.region] if args.region else list(REGIONS)
    for name in targets:
        if name not in REGIONS:
            print(f"Unknown region {name!r}. Choices: {', '.join(REGIONS)}",
                  file=sys.stderr)
            sys.exit(2)
        run_one(name, REGIONS[name], args.out_dir)


if __name__ == "__main__":
    main()
