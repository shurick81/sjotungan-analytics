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
| `broker_agency`    | `brokerAgencyName`             | Agency only — broker name is not retained, see Privacy. |
| `listing_id`       | `listingId`                    | Dedup key.                                                |
| `hemnet_url`       | constructed from `slug`        | `https://www.hemnet.se/salda/{slug}`.                     |

## Filtering rule

Keep only rows where `street_number` is between 6 and 122 inclusive.
This excludes #4, which belongs to a neighbouring BRF, and naturally
excludes any non-Myggdalsvägen results that happened to match the
location ID. All buildings on Myggdalsvägen in this BRF are
even-numbered; no odd numbers exist in the source data.

## Output

- `data/apartment_prices/sjotungan_sales_hemnet.csv` — filtered, sorted by `sold_date`
  descending, UTF-8 with BOM (so Excel renders Swedish characters),
  comma-separated, header row.
- `data/apartment_prices/sjotungan_sales_hemnet_raw.json` — every collected `SaleCard`,
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
| Tyresö kommun          | 17792       | `data/apartment_prices/tyreso_kommun_annual_medians_hemnet.csv`      |
| BRF Gäddan i Tyresö    | 485023      | `data/apartment_prices/gaddan_annual_medians_hemnet.csv`             |
| HSB BRF Siken i Tyresö | 485023      | `data/apartment_prices/siken_annual_medians_hemnet.csv`              |
| HSB BRF Björkbacken i Tyresö | 484982 + 484985 | `data/apartment_prices/bjorkbacken_annual_medians_hemnet.csv` |

Street-level files are not BRF-filtered (they cover every `<street> N`
sale Hemnet exposes and span multiple BRFs — used as neighborhood
market references). The kommun-level file is the broader Tyresö
market reference. The BRF Gäddan and BRF Siken files both use the
Sikvägen street query (same `location_id` as the Sikvägen aggregate)
but apply disjoint building-number allow-lists to isolate each
föreningens own addresses (see the per-BRF filter sections below).
BRF Björkbacken spans **two** streets and is fetched via the
`--source` multi-source mode (one query per street, results unioned
before aggregation).

## BRF Gäddan filter

BRF Gäddan i Tyresö occupies a non-contiguous subset of Sikvägen
(postnummer 135 41 Tyresö). The remaining numbers on Sikvägen belong
to BRF Siken or other föreningar and must be excluded. The script's
`--number-set` flag takes a comma-separated explicit allow-list; for
Gäddan the rule is:

> Address is on Sikvägen AND number is in
> `{29, 31, 33, 35, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 51, 53, 55, 59}`.

Source ranges this set encodes (from the föreningens own address list):

- Sikvägen 29–33, 35–39, 41–45, 47–55 odd (excludes 57)
- Sikvägen 59
- Sikvägen 38–42, 44–48 even

Excluded as not part of BRF Gäddan: Sikvägen 1–27, Sikvägen 30–36
even, and any address not on Sikvägen. The street-name regex already
discards non-Sikvägen results that may match the location ID.

## HSB BRF Siken filter

HSB BRF Siken i Tyresö covers the odd side of Sikvägen at the lower
end of the street (postnummer 135 41 Tyresö), 208 apartments built
1961–1963. The allow-list is:

> Address is on Sikvägen AND number is in
> `{1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23, 25, 27}`.

Excluded as not part of BRF Siken: every even number on Sikvägen
(2–30 belongs to BRF Laken), Sikvägen 29 and higher on either side
(BRF Gäddan), and any address not on Sikvägen. The Siken and Gäddan
allow-lists are disjoint by construction so the two output files can
safely be summed or compared without double-counting.

## HSB BRF Björkbacken filter

HSB BRF Björkbacken i Tyresö (postnummer 135 40 Tyresö) spans two
streets and is fetched via the `--source` multi-source mode — one
query per street, each with its own `location_id` and number set, and
results unioned before computing annual medians. Sources:

| Street            | location_id | Allow-list                                                    |
|-------------------|------------:|---------------------------------------------------------------|
| Björkbacksvägen   | 484982      | All odd numbers from 9 through 83 (i.e. 9, 11, 13, …, 81, 83) |
| Bollmoravägen     | 484985      | 36–58 inclusive                                               |

Why these ranges:

- **Björkbacksvägen 9–83 odd.** Individual lookups confirm at least
  9, 11, 17, 23, 25, 31, 33, 37, 49, 69, 75, 77, 83 as Björkbacken
  addresses (small 6-apt buildings, byggår 1964, all on the odd
  side). The registered föreningssäte is at #83. The pattern (small
  byggår-1964 buildings on the odd side, all in this number range)
  indicates the BRF covers the full odd run from 9 through 83.
  Björkbacksvägen 2 and 4 are explicitly excluded — they are a
  kommun-run stödboende, not part of the BRF — and so are all even
  numbers on the street.
- **Bollmoravägen 36–58.** Three energy declarations cover the
  buildings at 36–40, 42–46, and 54–58, and the styrelselokal is at
  #52. Bollmoravägen has many other BRFs (Gösen at 2/4/10/12,
  Solhöjden, Pluto at 154, plus 90 and 102 in unrelated listings),
  so the filter is strict: only 36–58 inclusive on Bollmoravägen
  belongs to Björkbacken.

The Björkbacksvägen and Bollmoravägen sources have disjoint
`location_id`s, so listings cannot be double-counted across sources.
Within each source the per-street number filter excludes neighbouring
BRFs sharing the same street.

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

When interpreting `data/apartment_prices/tyreso_kommun_annual_medians_hemnet.csv` (sharded
output), all 13 covered years (2013–2026) should be treated as complete
within Hemnet's overall coverage; 2026 is partial only because the
year is not yet over. Re-run periodically to refresh.

## Booli supplement

Booli has a separate sold-listings dataset for this BRF at
`https://www.booli.se/bostadsrattsforening/267023` (housingCoopId
267023 = HSB BRF Sjötungan). Unlike Hemnet, Booli scopes natively to
the BRF, so no street-number filter is needed.

Behind Cloudflare like Hemnet — plain `requests` returns 403. The
scraper at `extraction/scripts/scrape_booli_sales.py` uses Playwright
+ stealth: it loads the BRF page once and intercepts the
`getHousingCoopSold` GraphQL response, which returns the full
sold-listing history in a single call — no pagination, no result cap,
no shard-by-rooms.

Output:

- `data/apartment_prices/sjotungan_sales_booli.csv` — per-listing rows,
  same columns as the Hemnet CSV plus `booli_url` (in place of
  `hemnet_url`) and `apartment_number` (Lgh, populated on ~42% of rows).
- `data/apartment_prices/sjotungan_sales_booli_raw.json` — the full
  GraphQL `data.sold` array for re-parsing without re-fetching.

Coverage as of May 2026: 372 sales spanning 2012-11-02 → 2026-05-05 —
one year deeper than Hemnet's 2013-05-27 floor and 36 rows more
overall. Three columns the GraphQL endpoint does not expose are left
null: `asking_price_kr`, `monthly_fee_kr`, `price_change_pct`.
Recovering them requires one fetch per `/bostad/{booliId}` detail
page (~10 minutes for all 372; not implemented).

Hemnet and Booli are kept in separate CSVs, distinguished by the
`_hemnet` / `_booli` filename suffix. The two sources do not align
cleanly on (date, address, final_price): only 114 of the 336 Hemnet
rows have an exact-match Booli row, the rest differ mostly by small
date offsets (Hemnet appears to use kontraktsdatum, Booli
tillträdesdatum or similar). Any cross-source merge needs fuzzy
matching — date window, normalized address, price tolerance.

### Booli aggregate-only mode for peer BRFs

The Booli scraper supports `--aggregate-only --output-aggregated PATH`,
mirroring Hemnet's aggregate mode: it computes annual median / mean /
min / max kr/m² and writes a single time-series CSV with no per-row
data and no raw JSON. Used to source the peer-BRF comparison overlays
on `sales_booli.html`:

| Scope                   | housingCoopId | Output                                                |
|-------------------------|--------------:|-------------------------------------------------------|
| HSB BRF Björkbacken     | 53246         | `data/apartment_prices/bjorkbacken_annual_medians_booli.csv` |
| HSB BRF Gäddan i Tyresö | 48924         | `data/apartment_prices/gaddan_annual_medians_booli.csv`      |
| HSB BRF Siken           | 48115         | `data/apartment_prices/siken_annual_medians_booli.csv`       |

In every case Booli's coverage exceeds Hemnet's per-BRF aggregate:
~30–50 % more rows per year and one extra year (2012) at the start.
No street-number allow-list is needed — Booli's BRF endpoint already
scopes to the BRF natively, so multi-source unioning (Björkbacken)
and disjoint number-set filtering (Gäddan, Siken) are both unnecessary.

Tyresö kommun has no equivalent on Booli — `getHousingCoopSold` is
BRF-scoped only — so the kommun-level overlay on `sales_booli.html`
stays Hemnet-derived.

## Privacy

Both scrapers strip personal data on write:

- Hemnet: `brokerName` and `brokerThumbnail` are removed from each
  `SaleCard` before the raw JSON is written; `broker_name` is not a
  CSV column.
- Booli: the `agent` object (name, profile URL, photo URL) and
  `agentId` are removed from each `SoldProperty` before the raw JSON
  is written; `broker_name` is not a CSV column.
- Kept: `broker_agency` / `agency` (a legal entity, not personal data)
  and the agency logo (`brokerAgencyThumbnail` on Hemnet).

Replicating publicly-visible agent names into a separate dataset is
processing of personal data under GDPR Art. 4(2), and we have no
lawful basis (Art. 6) for retention. The scrubbing also runs on
`--from-cache`, so re-running over an old un-sanitised cache rewrites
it clean.

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
re-derive the CSV from `data/apartment_prices/sjotungan_sales_hemnet_raw.json`.

## Cloudflare and `--use-playwright`

As of April 2026 Hemnet runs Cloudflare's challenge mitigation
("Vänta..." / "Just a moment...") on every request that doesn't carry
a valid `cf_clearance` cookie. Plain `requests` returns HTTP 403 on
all paths — including `robots.txt` — regardless of headers, IP, or
ASN. The block is path-, header-, and IP-agnostic; the only signal
Cloudflare accepts is completing the JS challenge.

Pass `--use-playwright` to fetch via headless Chromium with stealth
tweaks. This earns a `cf_clearance` cookie on the first request and
reuses it across all subsequent pages in the same browser context, so
the JS challenge is paid once per scrape, not once per page. Costs:
~10× slower per page (Chromium overhead), and one-time install of
`playwright` + `playwright-stealth` + the Chromium binary (~150 MB).

Setup:

```bash
.venv/bin/pip install playwright playwright-stealth
.venv/bin/python -m playwright install chromium
```

If Hemnet later eases the policy back to header-only checks, the
plain-HTTP path will work again and `--use-playwright` becomes
optional.
