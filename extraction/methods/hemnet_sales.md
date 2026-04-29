# Hemnet sold-listings extraction (BRF Sjötungan)

Scrapes all sold listings ("slutpriser") for Myggdalsvägen, Tyresö from
Hemnet, filters to BRF Sjötungan's address range (#6–#122, even numbers
only), and writes a CSV plus a raw JSON archive.

## Source

Hemnet slutpriser, filtered by the street-level location ID for
Myggdalsvägen, Tyresö:

```
https://www.hemnet.se/salda/bostader?location_ids%5B%5D=485045&item_types%5B%5D=bostadsratt&by=sold_at&order=desc&page={N}
```

`location_ids[]=485045` is verifiable on a one-shot basis via
`https://www.hemnet.se/locations/show?q=Myggdals` — should return a
Street entry with `id: 485045`. If this changes, update the constant
in the script.

Pagination is incremental, page=1 upward, until a fetched page yields
zero new SaleCard objects. As of April 2026: ~8 pages × 50 listings =
351 raw cards, deduplicated and filtered to 334 rows.

## Why __NEXT_DATA__ and not rendered HTML

Hemnet is a Next.js app. The full structured listing payload is
embedded in each HTML page as JSON inside:

```html
<script id="__NEXT_DATA__" type="application/json">{...}</script>
```

The script extracts that JSON and walks it recursively for objects with
`__typename == "SaleCard"`. Each `SaleCard` carries the fields the page
renders (price, area, rooms, broker, etc.) plus a stable `listingId`
used for dedup. Parsing the JSON directly is far more robust than
parsing rendered DOM, which changes with every Hemnet redesign.

## Field mapping

| CSV column         | Source key (`SaleCard`)        | Notes                                                    |
|--------------------|--------------------------------|----------------------------------------------------------|
| `sold_date`        | `soldAt`                       | Unix timestamp (seconds, sometimes string-encoded float). Converted to `YYYY-MM-DD`. |
| `address`          | `streetAddress`                | e.g. "Myggdalsvägen 102".                                |
| `street_number`    | parsed from `streetAddress`    | Integer; regex `Myggdalsv[äa]gen\s+(\d+)`.               |
| `rooms`            | `rooms`                        | String like "3 rum"; parsed to float. May be null.       |
| `living_area_m2`   | `livingArea`                   | String like "75,5 m²"; parsed to float (Swedish comma).  |
| `final_price_kr`   | `finalPrice`                   | String "2 000 000 kr"; non-digits stripped to int.       |
| `asking_price_kr`  | `askingPrice`                  | Same.                                                    |
| `kr_per_m2`        | `squareMeterPrice`             | Same.                                                    |
| `monthly_fee_kr`   | `fee`                          | Same. Reflects the avgift at sale time, not today's.     |
| `price_change_pct` | `priceChange`                  | "+5,2 %" → 0.052; "±0 %" → 0.0.                          |
| `broker_agency`    | `brokerAgencyName`             |                                                           |
| `broker_name`      | `brokerName`                   | Often null.                                               |
| `listing_id`       | `listingId`                    | Dedup key.                                                |
| `hemnet_url`       | constructed from `slug`        | `https://www.hemnet.se/salda/{slug}`.                     |

## Filtering rule

Keep only rows where `street_number` is between 6 and 122 inclusive.
This excludes #4, which belongs to a neighbouring BRF, and naturally
excludes any non-Myggdalsvägen results that happened to match the
location ID. All buildings on Myggdalsvägen in this BRF are
even-numbered; no odd numbers exist in the source data.

## Output

- `data/apartment_prices/sjotungan_sales.csv` — filtered, sorted by `sold_date`
  descending, UTF-8 with BOM (so Excel renders Swedish characters),
  comma-separated, header row.
- `data/apartment_prices/sjotungan_sales_raw.json` — every collected `SaleCard`,
  unfiltered, for re-parsing without re-fetching.

## Coverage

Hemnet's slutpriser archive for this street goes back to 2013-05-27.
Earlier transfers exist but are not exposed publicly by Hemnet.
Possible supplements: Booli (similar coverage; cf. Booli notes below)
and Lantmäteriet (authoritative property register, paid lookups).

## Aggregate-only mode (reference streets)

For peer streets where we only need an annual time series rather than
the full per-sale dataset, use `--aggregate-only`. The script fetches
the same `SaleCard` payloads but writes only one CSV with annual median
`kr/m²` and a few sanity columns:

```
year,n,median_kr_per_m2,mean_kr_per_m2,min_kr_per_m2,max_kr_per_m2
```

No per-listing CSV and no raw JSON archive are written, so re-running
costs a fresh fetch (idempotent). Re-aggregate with the explicit
invocations in the README — there is no intermediate cache to fall
back to.

Reference streets currently maintained in this mode:

| Scope                  | Location ID | Output                                       |
|------------------------|------------:|----------------------------------------------|
| Sikvägen, Tyresö       | 485023      | `data/apartment_prices/sikvagen_annual_medians.csv`           |
| Björkbacksvägen, Tyresö | 484982     | `data/apartment_prices/bjorkbacksvagen_annual_medians.csv`    |
| Tyresö kommun          | 17792       | `data/apartment_prices/tyreso_kommun_annual_medians.csv`      |

Street-level files are not BRF-filtered (they cover every `<street> N`
sale Hemnet exposes and span multiple BRFs — used as neighborhood
market references). The kommun-level file is the broader Tyresö
market reference.

### Hemnet's 2,500-result cap

Hemnet caps slutpriser browsing at **50 pages × 50 listings = 2,500
results** per location query, regardless of how many sales exist. For
narrow queries (single street) this is unlikely to bite. For broader
ones it does:

- Sikvägen Tyresö → 512 listings, full 2013–2026 coverage.
- Björkbacksvägen Tyresö → 131 listings, full 2013–2026 coverage.
- Tyresö kommun unsharded → exactly 2,500 listings, dataset starts
  **mid-September 2019**. Recent years complete; earliest year partial.
- Tyresö kommun sharded by rooms → 4,142 listings, full 2013–2026
  coverage (see below).
- Sweden as a whole (location_id 17691) → caps at ~8 days of data.
  Country-level aggregation through this scraper is not feasible; use
  SCB instead (see `methods/sweden_brf_prices.md`).

### `--shard-by-rooms`: extending coverage past the 2,500 cap

Hemnet ignores `order=asc` and `sold_age` URL params (silently returns
descending-by-date), so sharding by **room count** is the only way to
expand past 2,500. The script's `--shard-by-rooms` flag runs one
unfiltered baseline sweep followed by one sweep per room category:
1, 2, 3, 4, 5, and 6+ rooms (`rooms_min`/`rooms_max` URL params). Each
shard hits its own 2,500-cap independently; results are deduplicated
by `listingId`.

Hemnet's `rooms_min`/`rooms_max` filter is **exact-integer match**, so
listings with null `rooms` or fractional values like "3,5 rum" are
excluded from every numeric shard. The unfiltered baseline shard
captures these (within its own 2,500 window) so recent years stay
complete. As a sanity check: 2020 returns 486 listings sharded vs. 486
unsharded — same value, indicating the baseline catches what the room
shards miss for years still in its window.

When interpreting `data/apartment_prices/tyreso_kommun_annual_medians.csv` (sharded
output), all 13 covered years (2013–2026) should be treated as complete
within Hemnet's overall coverage; 2026 is partial only because the
year is not yet over. Re-run periodically to refresh.

## Booli supplement (open)

Booli has a separate sold-listings dataset for this BRF at
`https://www.booli.se/bostadsrattsforening/267023`. Booli is gated by
Cloudflare's JS challenge, so HTTP scraping with `requests` returns 403.
Practical paths:

1. Run an extraction snippet in the user's browser DevTools console
   (cookies inherit Cloudflare clearance) and download the JSON.
2. Use Playwright with a stealth profile (heavy, brittle).
3. Save full HTML manually per slutpriser page and parse offline.

No script for Booli is committed yet. When added, it should write to
`data/apartment_prices/sjotungan_sales_booli.json` and the merged result into
`data/apartment_prices/sjotungan_sales.csv` with a `source` column.

## Known caveats

- One row (Myggdalsvägen 122, 2015-09-22, 88.5 m²) has a null `rooms`
  field because the broker omitted it on the original listing. Not
  imputed. The `hemnet_url` for that listing is in the CSV if a manual
  lookup is needed.
- `monthly_fee_kr` is the fee at the time of the sale, not today's.
  This is the right value for historical analysis.

## Reproducibility

The script is idempotent — re-running fetches Hemnet again and
overwrites both files. Use `--from-cache` to skip the network and
re-derive the CSV from `data/apartment_prices/sjotungan_sales_raw.json`.
