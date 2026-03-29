# Stamma Attendance Extraction Method

## Goal

Extract the number of members participating at the annual meeting from protocol PDFs.

Primary target is wording in the voting-roll section (for example `Godkännande av rostlangd`), such as:

- `Antal narvarande rostberattigade var 100 medlemmar samt 4 fullmakter.`

## Output schema

Rows are written to `data/general_states.csv` as two rows per year (categories 6 and 7) using this format:

```csv
year,category_id,value,file,page,x,y,width,height
```

Field notes:

- `year`: protocol year (for example 2025).
- `category_id`: `6` for present voting members, `7` for proxies.
- `value`: extracted numeric value printed in the protocol.
- `file`: filename in `data/stamma_protocols/`.
- `page`: page containing the attendance phrase.
- `x,y,width,height`: word-level highlight coordinates for the numeric value when found.

## Detection logic

1. Read page count with `pdfinfo`.
2. Extract each page text with `pdftotext -f N -l N`.
3. Normalize text for OCR/PDF text-layer noise:
   - lowercase
   - remove accents/diacritics
   - collapse whitespace
4. Search phrase patterns around:
   - `antal narvarande rostberattigade ... var <X> medlemmar ... <Y> fullmakter`
   - `narvarande rostberattigade var <X> medlemmar ...`
   - fallback `<X> medlemmar samt <Y> fullmakter`
5. If no text-layer match is found, run OCR fallback per page:
   - rasterize page with `pdftoppm`
   - OCR text + TSV word boxes with `tesseract`
   - run the same phrase patterns on OCR text
6. Emit the first matched row with page/evidence.

## Coordinates from OCR

- For text-layer PDFs: coordinates come from `pdftotext -bbox-layout`.
- For scanned PDFs: coordinates come from OCR TSV word boxes.
- OCR pixel coordinates are converted to PDF points using `72 / DPI` (default DPI=300).
- If proxy count is implied as `0` but not explicitly printed, proxy bbox stays empty.

## Write safety

- `--append` now sanitizes malformed CSV rows (ignores overflow fields).
- Writes are atomic via temporary file + replace, so a failure cannot leave a truncated CSV.

## Script

Dry-run:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_stamma_attendance.py 2025 stamma2025-protokoll.pdf
```

Append/upsert to CSV:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_stamma_attendance.py 2025 stamma2025-protokoll.pdf --append
```

## Verification checklist

1. Confirm `page` points to section `Godkannande av rostlangd` or equivalent attendance section.
2. Confirm evidence phrase includes both member and (if available) proxy counts.
3. Confirm numbers match the protocol text exactly.
4. Re-run with `--append` and verify idempotent update (same `meeting_year` + `file` row is replaced, not duplicated).
