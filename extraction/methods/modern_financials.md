# Modern annual report — financial_states & financial_events

Script: [`scripts/extract_modern_financials.py`](../scripts/extract_modern_financials.py)

Target: HSB Brf Sjötungan annual reports from `stamma2026.pdf` onward, where the Resultaträkning, Balansräkning, Kassaflödesanalys and Noter use the standard two-column "2025-01-01–2025-12-31 / 2024-01-01–2024-12-31" layout. Earlier years use different layouts and are covered by the legacy extractors in [`financial_states_events_legacy.md`](financial_states_events_legacy.md).

## Pipeline

1. Per-spec PDF page is read with `pdftotext -layout`.
2. The label regex is matched against each layout line.
3. The current-year column value is parsed from the matched line (split on 2+ whitespace, then keep integers; small note-reference digits ahead of the first 4+-digit value are dropped).
4. The bbox of the value tokens is located via `pdftotext -bbox-layout`, anchored on the same y-row as the label (first 4–6 label tokens chained together to disambiguate repeating starters like `Övriga` / `Varav` / `Ökning(+)/Minskning`).
5. **Bboxes are scaled by `CANVAS_SCALE = 1.5`** before writing — the HTML viewer renders PDF.js at 1.5× points, and all historical rows are stored pre-scaled. `pdftotext` returns raw PDF points (72 DPI), so the extractor multiplies x, y, width, height by 1.5 to match the canvas convention shared with [`fix_zero_coordinates.py`](../../fix_zero_coordinates.py).
6. After the regex pass, **aggregated leaves** (cat 18, 28) and **synthetic sub-parents** (cat 2) are derived (see below).
7. CSVs are read with their existing line terminator preserved (some are LF, some CRLF) so the diff stays clean.

## Aggregated leaves

Some leaf categories in [`event_categories.csv`](../../data/event_categories.csv) correspond to *several* PDF lines that the modern annual report doesn't subtotal. The script extracts each sub-line via its own regex and sums them — the row's bbox is the union of the matched lines, so highlight overlays cover all components.

| Cat | Name | Sub-lines in PDF (Not n) |
|---|---|---|
| 18 | Hyror | Hyror bostäder + Hyror garage och p-platser + Hyror lokaler + Hyror övrigt (Not 2) |
| 28 | Fastighetsskatt och fastighetsavgift | Fastighetsavgift bostäder + Fastighetsskatt lokaler (Not 4) |

To add a new aggregated leaf, extend `AGGREGATED_SPECS` in the script with a list of `Spec` entries — one per sub-line.

## Synthetic sub-parents

Some categories exist as parents in the hierarchy but are *not* printed as a single line in the modern PDF — only their children are. The script computes them automatically *after* the leaf extraction so the hierarchy validation always closes:

| Cat | Name | Derived as | Why synthetic |
|---|---|---|---|
| 2 | Fastighetsskötsel och lokalvård | Σ(50, 51, 52, 53, 54, 55) | Not 4 lists `Fastighetsskötsel`, `Städning`, `Hisstillsyn`, `Tillsyn…`, `Trädgårdsskötsel`, `Snöröjning` under a visual "Drift" heading but does not print a subtotal for the group |

The synthetic row's bbox is the union of the children's already-scaled bboxes, so highlight overlays land on the correct group of leaf rows. Synthetic rows are skipped if any required child is missing — a missing leaf is its own bug to fix, and faking the parent in that case would hide it.

Historical inconsistency this fixes: 2023 has cat 2 stored, 2024 didn't, 2025 originally didn't either. With the synthetic step the modern years now consistently include it.

To add a new synthetic sub-parent, extend `SYNTHETIC_SUBPARENTS` in the script: `parent_id -> [child_ids]`.

## Validation — always runs after extraction

The script ends with two independent reconciliations. Both must hold for the data to be trusted; neither requires anything outside the two CSVs and [`data/event_categories.csv`](../../data/event_categories.csv).

### 1. Event hierarchy

For every parent in `event_categories.csv`, the children's amounts must sum to the parent within tolerance:

```
cat 0  Nettoomsättning           = Σ(17 Årsavgifter, 18 Hyror, 19 Övr.intäkter, 20 Bortfall)
cat 1  Driftkostnader            = Σ(21..32, 2 Fastighetsskötsel-och-lokalvård sub-parent)
cat 2  Fastighetsskötsel-…       = Σ(50..55)
cat 5  Övriga externa kostnader  = Σ(33..43)
cat 6  Personalkostnader         = Σ(44..49)
```

Missing children are treated as zero (some categories — Bevakningskostnader, Konsultkostnader when nothing was incurred, etc. — legitimately do not appear in the source). A non-zero diff means an extraction error in the parent, in a child, or that a non-zero child was missed.

### 2. State vs event identities (current year vs prior year in `financial_states.csv`)

Two cross-table identities that must hold for any year with both prior-year and current-year state rows:

```
A)  Δ(short loan + long loan)    =  event 3 (Kassaflöde finansieringsverksamheten)
B)  Δ(likvida medel)             =  Σ cash-flow events (0, 4, 1, 5, 6, 7, 8, 3, 11, 12, 13, 14)
```

Avskrivning is *not* stored as an event — it is non-cash and would cancel between the P&L deduction and the kassaflöde addback, so summing the stored events already gives the correct cash flow. The same reason prevents a clean `Δequity == årets resultat` check from these CSVs alone; we leave that off rather than ship a misleading identity.

Tolerance is 5 SEK (covers cash-vs-accrual rounding on the net-interest event).

## Usage

Dry-run (prints rows + validations, no CSV writes):

```bash
.venv/bin/python extraction/scripts/extract_modern_financials.py 2025 stamma2026.pdf
```

Append and validate:

```bash
.venv/bin/python extraction/scripts/extract_modern_financials.py 2025 stamma2026.pdf --append
```

CI/automation mode (exit non-zero on any spec or validation failure):

```bash
.venv/bin/python extraction/scripts/extract_modern_financials.py 2025 stamma2026.pdf --append --strict
```

Skip validation (rarely useful — only for diagnosing the parser itself):

```bash
.venv/bin/python extraction/scripts/extract_modern_financials.py 2025 stamma2026.pdf --skip-validation
```

## Adding a new year

1. Verify the PDF layout matches stamma2026's structure. Sanity-check page numbers: `pdftotext -layout -f N -l N <pdf> -` for the Resultaträkning, Balansräkning, Kassaflöde, and Noter pages — they may shift if the introductory section grows or shrinks.
2. If page numbers shifted, edit `SPECS_STATES` / `SPECS_EVENTS` in the script.
3. Run with `--append --strict`. Validation failures will pinpoint missing or wrong rows.
4. If a sub-parent like cat 2 (Fastighetsskötsel och lokalvård) is needed for hierarchy completeness — i.e. its children 50–55 are extracted but the parent itself isn't a printed line — add it as a synthetic row manually after the run; the hierarchy validator will then pass cleanly.

## Why two CSVs at all

`financial_states.csv` captures balance-sheet snapshots; `financial_events.csv` captures the period's flows. The validation block above is the contract between them — if the identities hold, downstream consumers can trust that the income statement, balance sheet and cash flow are internally consistent regardless of which year was extracted last.
