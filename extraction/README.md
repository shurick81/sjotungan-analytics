# Extraction Workspace

This folder groups reusable data-extraction assets in one place.

## Structure

- `scripts/` — executable extraction scripts.
- `methods/` — methodology documents, assumptions, and verification steps.

## Current assets

- Script: `scripts/extract_motion_resolutions.py`
- Method doc: `methods/motion_resolutions.md`
- Script: `scripts/extract_motion_protocol_decisions.py`
- Script: `scripts/extract_stamma_attendance.py`
- Method doc: `methods/stamma_attendance.md`
- Script: `scripts/extract_board_leadership.py`
- Method doc: `methods/board_leadership.md`
- Script: `scripts/extract_legacy_states.py`
- Script: `scripts/extract_legacy_events_candidates.py`
- Script: `scripts/extract_pre2009_states_events.py`
- Method doc: `methods/financial_states_events_legacy.md`
- Script: `scripts/check_soliditet_readiness.py`
- Script: `scripts/calculate_soliditet.py`
- Script: `scripts/extract_soliditet_states_candidates.py`
- Method doc: `methods/soliditet.md`
- Reference: `methods/domain_conventions.md` (M-numbers, abbreviations)
- Script: `scripts/compare_guide_html_pdf.py`
- Method doc: `methods/guide_html_pdf_comparison.md`
- Script: `scripts/scrape_hemnet_sales.py`
- Script: `scripts/scrape_booli_sales.py`
- Method doc: `methods/sjotungan_sales.md`
- Script: `scripts/fetch_scb_brf_prices.py`
- Method doc: `methods/sweden_brf_prices.md`
- Script: `scripts/fetch_maklarstatistik_brf.py`
- Method doc: `methods/maklarstatistik_brf.md`

## Macro data note (inflation)

Inflation (KPI) is not extracted from Sjotungan PDFs. It is maintained as a local macro snapshot in `data/macro_sweden.json` at repo root.

- Current range: 1980-2024.
- Feasible extension: at least back to 1980 from SCB annual KPI data.
- Keep source attribution and `updated_at` in the JSON file whenever the range is expanded.

## Reusing the method for other documents

The method in `methods/motion_resolutions.md` now documents a reusable 6-stage pipeline:

1. Define entity and schema.
2. Build phrase library.
3. Resolve context around matches.
4. Use OCR as targeted enrichment.
5. Normalize and deduplicate.
6. Verify with evidence.

When adapting extraction to a new source (for example annual report notes or meeting protocols), keep the pipeline and swap only domain-specific anchors, phrase patterns, and normalization labels.

## Usage

Dry-run motion resolution extraction:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2025 stamma2025.pdf
```

Append extracted rows to `data/motions.csv`:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2025 stamma2025.pdf --append
```

Extract protocol outcomes and whether the stamma followed styrelsens forslag (all years in `data/motions.csv`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_protocol_decisions.py --all-years --append
```

Extract from all protocol PDFs (including years missing in `data/motions.csv`) and bootstrap missing motion rows:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_protocol_decisions.py --all-protocols --bootstrap-missing --append
```

Single year + protocol PDF:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_protocol_decisions.py 2024 protokoll2024.pdf --append
```

Dry-run attendance extraction from protocol:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_stamma_attendance.py 2025 stamma2025-protokoll.pdf
```

Append extracted attendance rows to `data/general_states.csv` (categories 6 and 7):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_stamma_attendance.py 2025 stamma2025-protokoll.pdf --append
```

The script writes directly to `data/general_states.csv` as:

- category `6`: Narvarande rosterattigade medlemmar
- category `7`: Fullmakter

For older scanned protocols without a text layer, `extract_stamma_attendance.py` now uses OCR fallback (`pdftoppm` + `tesseract`) before writing results.

## Board leadership extraction (annual reports)

Dry-run board leadership extraction (writes artifacts under `extraction/artifacts/board_leadership/`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py
```

Run only selected years (useful when validating `Suppleanter` in 2015):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py --years 2015
```

Append board leadership rows to `data/general_states.csv` (categories 0, 1, 2, 3, 4, 8, 9):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py --append
```

The script now also extracts `Suppleanter` (category `8`) and signed-auditor names under the audit report section (category `9`).

## Legacy financial extraction (states/events)

The reusable method for backfilling older financial years is documented in:

- `methods/financial_states_events_legacy.md`

It describes a two-phase approach:

1. OCR extraction with per-year config.
2. Invariant-based verification before append.

Extract legacy states (dry run):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_states.py
```

Append legacy states:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_states.py --append
```

Extract legacy event candidates (dry run):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_events_candidates.py
```

Append legacy events (only after verification):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_events_candidates.py --append
```

## Guide HTML ↔ PDF comparison

Compare `brf-economy-guide.html` against `brf-economy-guide.pdf` to verify
text parity, paragraph ordering, and illustration/chart caption presence.

```bash
# Print diff report to stdout
python extraction/scripts/compare_guide_html_pdf.py

# Save report to file
python extraction/scripts/compare_guide_html_pdf.py -o /tmp/guide_diff.txt

# Verbose mode (show all paragraph alignments)
python extraction/scripts/compare_guide_html_pdf.py -v
```

Exit code 0 = no issues, 1 = differences found. See `methods/guide_html_pdf_comparison.md` for details.

Extract pre-2009 core states/events (2003, 2004, 2006, 2007, 2008):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_pre2009_states_events.py --append
```

## Soliditet readiness and calculation

Check whether `data/financial_states.csv` currently contains both required ingredients for soliditet (`eget kapital` and `summa tillgangar`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/check_soliditet_readiness.py
```

Compute yearly soliditet (decimal ratio) as a check only (stdout):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/calculate_soliditet.py
```

Extract candidate `eget kapital` + `summa tillgangar` rows from annual reports (dry-run to stdout):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_soliditet_states_candidates.py
```

Append only missing year/category rows into `data/financial_states.csv`:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_soliditet_states_candidates.py --append
```

If category auto-detection fails due to naming differences, pin IDs explicitly in both scripts:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/check_soliditet_readiness.py --equity-category-id <id> --assets-category-id <id>
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/calculate_soliditet.py --equity-category-id <id> --assets-category-id <id>
```

## Hemnet sold-listings extraction

Scrape all sold listings ("slutpriser") for Myggdalsvägen, Tyresö from Hemnet, filter to BRF Sjötungan's address range (#6–#122), and write `data/apartment_prices/sjotungan_sales_hemnet.csv` plus `data/apartment_prices/sjotungan_sales_hemnet_raw.json`. See `methods/sjotungan_sales.md` for the field mapping, filtering rule, coverage, and Booli supplement notes.

Scrape from the network:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py
```

Re-parse the CSV from the cached raw JSON without re-fetching:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py --from-cache
```

Aggregate-only mode for a reference street (no per-listing data persisted, only annual medians). Outputs are not BRF-filtered — they cover every sale on the named street and are used as neighborhood market references.

Sikvägen, Tyresö → `data/apartment_prices/sikvagen_annual_medians.csv`:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --location-id 485023 --street-name Sikvägen --no-filter \
    --aggregate-only --output-aggregated data/apartment_prices/sikvagen_annual_medians.csv
```

Björkbacksvägen, Tyresö → `data/apartment_prices/bjorkbacksvagen_annual_medians.csv`:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --location-id 484982 --street-name Björkbacksvägen --no-filter \
    --aggregate-only --output-aggregated data/apartment_prices/bjorkbacksvagen_annual_medians.csv
```

Tyresö kommun → `data/apartment_prices/tyreso_kommun_annual_medians_hemnet.csv` (use empty `--street-name` since the query spans many streets). Pass `--shard-by-rooms` to expand past Hemnet's 2,500-result cap by sweeping one unfiltered baseline plus one query per room count (1, 2, 3, 4, 5, 6+); without it the dataset starts in mid-September 2019. With sharding the kommun output covers 2013–2026 (≈4,100 listings):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --location-id 17792 --street-name "" --no-filter --shard-by-rooms \
    --aggregate-only --output-aggregated data/apartment_prices/tyreso_kommun_annual_medians_hemnet.csv
```

BRF Gäddan i Tyresö → `data/apartment_prices/gaddan_annual_medians_hemnet.csv`. Same Sikvägen query as the Sikvägen aggregate above, but with `--number-set` to restrict to the föreningens own addresses (see `methods/sjotungan_sales.md#brf-gäddan-filter` for the allow-list rationale). Add `--use-playwright` while Hemnet's Cloudflare challenge is active (see `methods/sjotungan_sales.md#cloudflare-and---use-playwright`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --location-id 485023 --street-name Sikvägen \
    --number-set "29,31,33,35,37,38,39,40,41,42,43,44,45,46,47,48,49,51,53,55,59" \
    --aggregate-only --output-aggregated data/apartment_prices/gaddan_annual_medians_hemnet.csv \
    --use-playwright
```

HSB BRF Siken i Tyresö → `data/apartment_prices/siken_annual_medians_hemnet.csv`. Same Sikvägen query, restricted to the odd side 1–27 (see `methods/sjotungan_sales.md#hsb-brf-siken-filter`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --location-id 485023 --street-name Sikvägen \
    --number-set "1,3,5,7,9,11,13,15,17,19,21,23,25,27" \
    --aggregate-only --output-aggregated data/apartment_prices/siken_annual_medians_hemnet.csv \
    --use-playwright
```

HSB BRF Björkbacken i Tyresö → `data/apartment_prices/bjorkbacken_annual_medians_hemnet.csv`. Spans two streets (Björkbacksvägen odd 9–83 and Bollmoravägen 36–58), fetched in multi-source mode with one `--source` per street; rows are unioned before aggregation (see `methods/sjotungan_sales.md#hsb-brf-björkbacken-filter`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_hemnet_sales.py \
    --aggregate-only --output-aggregated data/apartment_prices/bjorkbacken_annual_medians_hemnet.csv \
    --use-playwright \
    --source "484982:Björkbacksvägen:9,11,13,15,17,19,21,23,25,27,29,31,33,35,37,39,41,43,45,47,49,51,53,55,57,59,61,63,65,67,69,71,73,75,77,79,81,83" \
    --source "484985:Bollmoravägen:36,37,38,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,54,55,56,57,58"
```

## Booli sold-listings extraction

Booli's GraphQL endpoint (`getHousingCoopSold`) scopes natively to a
BRF (`housingCoopId`), unlike Hemnet which only filters down to street
level. A single call returns the BRF's full sold-listing history — no
pagination, no result cap, no street-number filter, no shard-by-rooms.
Same Cloudflare gating as Hemnet, so Playwright + stealth is built
into the scraper (no flag needed). See `methods/sjotungan_sales.md`
for the field mapping and the privacy rationale (broker name + photo
URL are stripped on write).

Per-listing extraction for BRF Sjötungan (default args):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_booli_sales.py
```

Writes `data/apartment_prices/sjotungan_sales_booli.csv` plus the raw
GraphQL payload at `..._booli_raw.json`.

Annual-medians-only mode for peer-BRF comparison overlays on
`sales_booli.html`:

```bash
# HSB BRF Björkbacken (housingCoopId 53246)
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_booli_sales.py \
    --brf-id 53246 --aggregate-only \
    --output-aggregated data/apartment_prices/bjorkbacken_annual_medians_booli.csv

# HSB BRF Gäddan i Tyresö (housingCoopId 48924)
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_booli_sales.py \
    --brf-id 48924 --aggregate-only \
    --output-aggregated data/apartment_prices/gaddan_annual_medians_booli.csv

# HSB BRF Siken (housingCoopId 48115)
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/scrape_booli_sales.py \
    --brf-id 48115 --aggregate-only \
    --output-aggregated data/apartment_prices/siken_annual_medians_booli.csv
```

Tyresö kommun has no equivalent on Booli — `getHousingCoopSold` is
BRF-scoped only — so the kommun-level overlay on `sales_booli.html`
stays Hemnet-derived.

## National BRF price reference (SCB)

Annual sold-tenant-owned-flats (BRF) prices for Sweden from the official SCB PxWeb API. Total median/average price in SEK thousands, 2000–2024. See `methods/sweden_brf_prices.md` — note that this is a **total price** series, not kr/m², so it is not directly unit-comparable to the per-m² Hemnet medians; normalize to an index before plotting alongside.

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/fetch_scb_brf_prices.py
```

Fetch a different region (e.g. Stockholm county = `01`) into a separate file:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/fetch_scb_brf_prices.py \
    --region 01 --output data/stockholm_county_brf_annual.csv
```

## Mäklarstatistik BRF kr/m² (national + Stockholm county)

SCB publishes BRF totals only and lags by ~12 months; Mäklarstatistik
publishes the canonical kr/m² series including the previous month. Use
this script for national and Stockholms län aggregates that cover 2026
to-date. See `methods/maklarstatistik_brf.md` for endpoint shape and
why pure monthly medians are not available.

Fetch all configured regions (writes 4 CSVs: annual + rolling12 each
for `sverige` and `stockholms_lan`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/fetch_maklarstatistik_brf.py
```

Fetch a single region:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/fetch_maklarstatistik_brf.py \
    --region sverige
```
