# Board Leadership Extraction Method

This method extracts annual-report board leadership rows into `data/general_states.csv` and stores extraction artifacts under `extraction/artifacts/board_leadership/`.

## Scope

- Target categories in `general_states.csv`:
  - `0`: Rakenskapsar period (`YYYY-01-01 – YYYY-12-31`)
  - `1`: Ordförande
  - `2`: Vice ordförande
  - `3`: Ledamoter (semicolon-separated)
   - `4`: Valberedning (semicolon-separated)
   - `8`: Suppleanter (semicolon-separated)
   - `9`: Revisorer signerat årsredovisningen (semicolon-separated)
- Target years: `2003` to `2018`

## Pipeline

1. Select source PDF per target year.
2. Scan early pages for a board-related section using keyword scoring (`styrelse`, `ordf`, `vice`, `ledamot`, `valbered`, `revisor`).
3. Read text layer with `pdftotext`; fallback to OCR (`pdftoppm` + `tesseract`) for scanned pages.
4. Parse lines for role patterns:
   - Ordförande
   - Vice ordförande
   - Ledamoter
   - Suppleanter
   - Valberedning
   - Signed auditors from the audit-report signature section
5. Persist artifacts for each year:
   - `YYYY_board_lines.txt` (evidence lines)
   - `YYYY_board_extraction.json` (parsed result)
6. Optional upsert into `data/general_states.csv`.

## Script

- `extraction/scripts/extract_board_leadership.py`

Dry run:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py
```

Single year validation (example):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py --years 2015
```

Append to `data/general_states.csv`:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py --append
```

## Notes

- For older scans, OCR quality varies by year and source PDF quality.
- Artifacts are intentionally saved for audit and iterative cleanup.