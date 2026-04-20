# Soliditet: Extraction and Calculation Method

This document defines how to add robust soliditet coverage from annual reports.

## Definition

Soliditet is calculated as:

$$
soliditet = \frac{eget\ kapital}{summa\ tillgangar}
$$

Use values from the same balance sheet year.

## Required source fields

To compute soliditet, `data/financial_states.csv` must include yearly rows for:

- `eget kapital`
- `summa tillgangar` (or equivalent `balansomslutning`)

If one of these categories is missing, soliditet is not derivable for that year.

## Category setup

Add or verify matching rows in `data/state_categories.csv` for:

- `Eget kapital`
- `Summa tillgangar`

Keep naming stable so scripts can resolve categories automatically.

## Workflow

1. Extract or backfill missing state rows (`eget kapital`, `summa tillgangar`) into `data/financial_states.csv`.
2. Run readiness check to verify both categories exist and overlap by year.
3. Calculate soliditet client-side in the webpage from extracted state rows.
4. Spot-check a handful of years against original PDF values.

## Scripts

### 1) Readiness check

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/check_soliditet_readiness.py
```

Expected output includes:

- resolved `equity_category_id`
- resolved `assets_category_id`
- `years_both`
- `ready=true|false`

If auto-detection fails, provide explicit IDs:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/check_soliditet_readiness.py \
  --equity-category-id <id> \
  --assets-category-id <id>
```

### 2) Soliditet calculation (validation only)

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/calculate_soliditet.py
```

If needed, pin category IDs:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/calculate_soliditet.py \
  --equity-category-id <id> \
  --assets-category-id <id>
```

Output schema:

- `year`
- `eget_kapital`
- `summa_tillgangar`
- `soliditet` (decimal ratio)

## Validation checks

After extraction is complete and before shipping page changes:

1. Ensure there are no years with missing denominator.
2. Verify `0 <= soliditet <= 1.5` for sanity (allowing exceptional years).
3. Manually verify at least 3 sampled years against PDF balance sheet rows.

## Notes

- Do not store derived soliditet in `data/`; keep `data/` for extracted source values from årsredovisning.
- The webpage should derive soliditet directly from `data/financial_states.csv` (categories 6 and 7).
- This method is intentionally separate from liquidity/debt-only states.
- If legacy scanned years are added, follow the OCR + invariant approach in `methods/financial_states_events_legacy.md`.
