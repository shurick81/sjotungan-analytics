# Legacy Financial States and Events Extraction Method

This method documents how to backfill older years from scanned annual reports and keep the output reusable and verifiable.

## Scope

- Outputs:
  - `data/financial_states.csv`
  - `data/financial_events.csv`
- Focus years:
  - Legacy scanned reports (currently 2011-2009 added for states)
- Input sources:
  - PDFs in `data/annual_reports/`
  - Category mapping in:
    - `data/state_categories.csv`
    - `data/event_categories.csv`

## Core Principle

Use a two-phase workflow:

1. Extract with OCR and stable per-year config.
2. Verify against accounting invariants before append.

This prevents one-off manual edits and makes improvements repeatable.

## Phase 1: Prepare and classify source PDFs

1. Confirm annual report file exists in `data/annual_reports/`.
2. Check whether PDF has text layer or is scanned:
   - Quick test with `pdftotext`.
   - If little/no text, use OCR path.
3. Identify pages for:
   - Balance sheet + relevant notes (states)
   - Income statement + cash flow statement + relevant notes (events)

## Phase 2: States extraction (recommended first)

States should be extracted before events because event validation depends on state deltas.

### Implemented script

- `extraction/scripts/extract_legacy_states.py`

### How it works

1. Per-year config stores:
   - PDF file
   - OCR column thresholds (`min_left_px`, `col_split_px`)
   - Amount keys and target categories
2. OCR each required page using `pytesseract.image_to_data`.
3. Group OCR tokens by y-position into rows.
4. Concatenate numeric fragments to match amount keys.
5. Convert OCR pixel coordinates into canvas coordinates:

$$
canvas = pixel \times \left(\frac{72}{DPI}\right) \times 1.5
$$

With `DPI=300`, scale is $0.36$.

6. Emit rows in `financial_states.csv` format.
7. Append only missing `(year, category_id)` keys.

### Usage

Dry run:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_states.py
```

Append:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_states.py --append
```

Custom years:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_states.py 2011 2010 --append
```

## Phase 3: Events extraction (candidate-first)

For legacy years, event representation can be tricky (accrual lines vs cashflow lines). Start with candidate extraction and verify before append.

### Implemented script

- `extraction/scripts/extract_legacy_events_candidates.py`

### Candidate workflow

1. Use per-year config for known amounts and pages.
2. OCR and locate coordinates with the same row-grouping logic as states.
3. Print candidate CSV rows (no write by default).
4. Reconcile candidate top-level sum against liquidity delta from states.
5. Only append after reconciliation is acceptable and mapping is agreed.

### Usage

Candidate dry run:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_events_candidates.py
```

Candidate append (only when verified):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_legacy_events_candidates.py --append
```

## Verification Checklist

Run these checks after each year batch.

### A. Coverage check

- `financial_states.csv` has expected years.
- `financial_events.csv` has expected years.

### B. States sanity

- Values for debt split and liquidity categories align with source report columns.
- Coordinate boxes are visible in UI at expected locations.

### C. Events invariant check

For year $Y$, verify top-level event sum equals liquidity change:

$$
\sum events_{top}(Y) = liquidity(Y) - liquidity(Y-1)
$$

Where:

$$
liquidity(Y) = state_2 + state_3 + state_4 + state_5
$$

Small rounding tolerance may be acceptable for OCR/statement rounding differences.

### D. Duplicate prevention

- Appenders should skip existing `(year, category_id)` rows.
- Keep one authoritative row per year/category.

## How to improve the method

1. Tighten OCR matching:
   - Add token confidence filtering.
   - Add fallback split for merged OCR tokens.
2. Better page detection:
   - Auto-detect statement pages from OCR anchors.
3. Config hardening:
   - Move per-year configs into YAML for easier review.
4. Add automated regression checks:
   - Year coverage assertions.
   - Reconciliation assertions.
5. Add visual QA helper:
   - Generate a preview image with drawn boxes for each extracted amount.

## Current status snapshot

- States:
  - Backfilled to 2009 in `data/financial_states.csv`.
- Events:
  - Candidate extractor exists for 2011-2009.
  - Final append should be done after reconciliation sign-off.

## Related files

- `extraction/scripts/extract_legacy_states.py`
- `extraction/scripts/extract_legacy_events_candidates.py`
- `data/financial_states.csv`
- `data/financial_events.csv`
- `data/state_categories.csv`
- `data/event_categories.csv`
