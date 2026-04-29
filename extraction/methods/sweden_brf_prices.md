# National BRF price reference (SCB)

Annual sold-tenant-owned-flats (BRF) prices for Sweden, used as a
country-level reference alongside the per-street Hemnet medians.

## Source

Statistiska centralbyrån (SCB), table
`BO/BO0501/BO0501C/FastprisBRFRegionAr` —
"Sold tenant-owned flats by region. Year 2000–2024".

PxWeb API endpoint:

```
https://api.scb.se/OV0104/v1/doris/en/ssd/BO/BO0501/BO0501C/FastprisBRFRegionAr
```

The script POSTs a JSON query body and parses the returned
`{columns, data}` payload into a flat CSV.

## Why SCB, not Hemnet

We initially planned to scrape Hemnet's own prisutveckling page for a
country-level median series. As of April 2026, Hemnet has dismantled
the public `/prisutveckling/...` URLs (404) and the slutpriser pages no
longer embed any aggregated time series in `__NEXT_DATA__` — only
per-listing `SaleCard` objects. The official SCB series replaces it
with better provenance and a stable JSON API.

## What's in the table

Variables (PxWeb codes):

- `Region` — 26 regions, including:
  - `00` Sweden (default)
  - `0010` Greater Stockholm
  - `0020` Greater Gothenburg
  - `0030` Greater Malmö
  - `01`–`25` län (counties)
- `ContentsCode`:
  - `BO0501R6` Number of sales
  - `BO0501R7` Average price (SEK thousands)
  - `BO0501R8` Median price (SEK thousands)
- `Tid` — year, 2000–2024.

## Important limitation: total price, not kr/m²

SCB publishes the average and median **total** sale price for BRFs by
region. SCB does **not** publish a public kr/m² series for BRFs (the
official price index `BO0501A` covers villas, fritidshus, and lantbruk
— but not BRFs). So the values in `data/apartment_prices/sweden_brf_annual.csv` are in
SEK and are *not* unit-comparable to the per-m² medians in
`data/apartment_prices/sikvagen_annual_medians.csv` /
`data/apartment_prices/bjorkbacksvagen_annual_medians.csv`.

For trend comparison, normalize to an index (e.g. divide every year by
the 2015 value) before plotting alongside the per-m² series.

For an actual kr/m² national series, the canonical source is
Valueguard's HOX-index (paid licence, Mäklarstatistik distributes it
free with a one-month delay on `maklarstatistik.se`). Not yet
extracted.

## Output

`data/apartment_prices/sweden_brf_annual.csv` — UTF-8 with BOM, columns:

```
year, region, n_sold, avg_price_kkr, median_price_kkr
```

The default region is `00` (whole Sweden). Re-run with `--region 01`
etc. to fetch a county-level series; pass `--output` to write to a
different file so you don't overwrite the national one.

## Reproducibility

Idempotent. Re-running fetches SCB again and overwrites the output.
SCB updates this table once per year (typically Q1 the year after).
