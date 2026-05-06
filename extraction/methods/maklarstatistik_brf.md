# Mäklarstatistik BRF kr/m² aggregates

National- and county-level published median kr/m² for BRFs
(bostadsrätter), used as the macro reference alongside the per-street
Hemnet medians.

## Why Mäklarstatistik (not Hemnet)

Hemnet's slutpriser browsing caps at 2,500 results per location query.
Even with room-count sharding (see `methods/sjotungan_sales.md`), country-
or county-level coverage by per-sale scraping is impractical: Sweden as
a whole caps at ~8 days of data, and Stockholms län would still risk
truncation across the longer history. Mäklarstatistik publishes the
finished aggregates that Hemnet's UI used to expose under
`/prisutveckling/...` (now 404). One HTTP call per region replaces
hundreds of paginated fetches.

Compared to SCB's `FastprisBRFRegionAr`, Mäklarstatistik is the
canonical kr/m² series (SCB only publishes total prices for BRFs) and
is current to the previous month, whereas SCB lags by ~12 months.

## Source

Mäklarstatistik is a WordPress site. The chart data is loaded by
admin-ajax POST:

```
POST https://www.maklarstatistik.se/wp-admin/admin-ajax.php
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest

action=price_trends_comparison
post_ids[]=<region post_id>
housing_tenure=bostadsratter
```

Region post_ids come from the shortlink (`?p=NNNN`) on each region
page. Currently mapped:

| Key              | post_id | Region page                                  |
|------------------|--------:|----------------------------------------------|
| `sverige`        | 6922    | `/omrade/riket/`                             |
| `stockholms_lan` | 5948    | `/omrade/riket/stockholms-lan/`              |

Add more by visiting the region's `/omrade/.../` URL and reading the
`?p=NNNN` from the canonical/shortlink, then extending `REGIONS` in
the script.

## Response shape

```jsonc
{
  "success": true,
  "data": {
    "year_by_year_labels": ["1996", "1997", ..., "2025"],
    "chart_data": {
      "year-by-year":  [{"data": [{"meta": "...", "value": "4997.00"}, ...]}],
      "12-months":     [{"data": [{"value": "45860.09"}, ...]}],
      "24-months":     [...],
      "36-months":     [...],
      "48-months":     [...]
    },
    "chartjs_data": { ... },        // same numbers without HTML meta
    "titles": ["Riket"],
    "column_count": 30,
    "legend": "..."
  }
}
```

Each `"data"` array carries 12 (for the 12-month series) or 30 (for
year-by-year, 1996–2025) median kr/m² values as decimal-string
`"value"` fields. Numbers are kr/m².

## Two output series

The script writes two CSVs per region:

### Annual (`data/apartment_prices/maklarstatistik_<region>_brf_annual.csv`)
```
year, kr_per_m2
1996, 4997
...
2025, 44894
```
- 30 rows, 1996–2025.
- Calendar-year median kr/m² for the whole region.
- Comparable across years; the most recent complete year is `<current
  year - 1>`. The most recent partial year (e.g. 2026) is **not** in
  this file — use `rolling12` for that.

### 12-month rolling (`data/apartment_prices/maklarstatistik_<region>_brf_rolling12.csv`)
```
period_label, period_end_year, period_end_month, kr_per_m2
apr -25, 2025, 4, 45860
...
mar -26, 2026, 3, 47544
```
- 12 rows; one per month in the trailing 12-month window.
- Each value is the **median over the 12 months ending at that
  label** (rolling, not the calendar month). The last row is the
  most recent published number — typically lagging the current
  month by ~1 month.
- Use this to read off the current price level for 2026 (the
  `mar -26` row above is the median over apr 2025 – mar 2026).

The script omits 24/36/48-month rolling series; not needed for our
analyses and trivially derivable from the same payload if they ever
are.

### Why no pure monthly medians?

Mäklarstatistik does not publish a per-month, non-rolling kr/m² series
on these pages. The closest thing is the rolling-12 series captured
above. If we ever need true monthly medians we'd have to compute them
ourselves from per-sale data — back to Hemnet/Booli scraping with all
the cap problems described in `sjotungan_sales.md`.

## Monthly labels

The 12 month-end labels (`apr -25` ... `mar -26`) are not in the AJAX
payload — they're rendered server-side into the page HTML on
`data-labels="..."` attributes. The script fetches the region page and
parses them out for the `br-12m-prisutveckling` chart.

`-25` is the 2-digit year, expanded to `2025` (we pin to the
21st-century window; this script will need a 2-digit-year fix in
~2099).

## Reproducibility

Idempotent. Re-running fetches both endpoints again and overwrites the
CSVs. Mäklarstatistik publishes one update per month around the 9th
of the following month; re-run monthly to refresh the rolling-12
series and to pick up new annual rows when a year is finalised.
