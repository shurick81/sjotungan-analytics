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

- `data/sjotungan_sales.csv` — filtered, sorted by `sold_date`
  descending, UTF-8 with BOM (so Excel renders Swedish characters),
  comma-separated, header row.
- `data/sjotungan_sales_raw.json` — every collected `SaleCard`,
  unfiltered, for re-parsing without re-fetching.

## Coverage

Hemnet's slutpriser archive for this street goes back to 2013-05-27.
Earlier transfers exist but are not exposed publicly by Hemnet.
Possible supplements: Booli (similar coverage; cf. Booli notes below)
and Lantmäteriet (authoritative property register, paid lookups).

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
`data/sjotungan_sales_booli.json` and the merged result into
`data/sjotungan_sales.csv` with a `source` column.

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
re-derive the CSV from `data/sjotungan_sales_raw.json`.
