# Motion Resolution Extraction Method

## Goal

Extract the board's recommendation on member motions from kallelse/arsredovisning PDFs.

Primary target phrases are in sections such as:

- Styrelsens yttrande
- Styrelsens slutsats

## Output schema

Rows are written to data/motions.csv with this format:

```csv
year,file,motion_number,page,title,authors,resolution,resolution_page,resolution_x,resolution_y,resolution_width,resolution_height
```

Field notes:

- motion_number: motion identifier in the meeting document (for example 1, 2).
- page: page where motion header was found (nearest MOTION N).
- resolution_page: page containing explicit recommendation sentence.
- resolution: normalized category.

## Normalized resolution values

- Tillstyrker
- Avstyrker
- Delvis tillstyrker
- Bifalls
- Avslås
- Besvarad
- Oklar (fallback/manual)

Rule of thumb:

- If source text says styrelsen yrkar/foreslar bifall or avslag, keep recommendation labels Tillstyrker/Avstyrker.
- If source text says motionen bifalles/avslas, keep outcome labels Bifalls/Avslås.

## Detection logic

1. Extract PDF text page-by-page with pdftotext.
2. Detect resolution sentence using recommendation regex patterns:
   - yrkar styrelsen ... bifall
   - yrkar styrelsen ... avslag/avslas
   - motionen bifalles/avslas
3. Backtrack from resolution_page to nearest MOTION N heading.
4. Run OCR on the motion header page to extract:
   - title (first meaningful heading lines after MOTION N)
   - authors (signer rows like Namn, Adress -> semicolon-separated names)
5. Emit one normalized row per detected motion recommendation.
6. Extract word-level bounding box for the resolution token from the evidence page using pdftotext -bbox-layout and store:
   - resolution_x
   - resolution_y
   - resolution_width
   - resolution_height
   - if text-layer lookup fails, use OCR word boxes as fallback

## How To Reuse This Method For Other Documents

Use this as a 6-stage extraction pipeline. The current script is one concrete implementation of this generic pattern.

### Stage 1: Define target entities and output schema

For each new extraction task, write down:

- Entity: what one output row represents (for example one motion, one decision, one cost line).
- Anchors: stable headings and marker phrases (for example MOTION N, RESULTATRÄKNING, NOTER).
- Output schema: destination CSV and required columns.
- Normalization set: closed vocabulary for category fields.

If the entity or vocabulary is unclear, extraction quality will drift.

### Stage 2: Build phrase library (detection patterns)

Create a phrase table with three classes:

- Strong patterns: direct, high-precision wording.
- Weak patterns: likely wording variants.
- Exclusions: phrases that should not match.

Store phrase variants with OCR-tolerant alternatives (a/o confusion, missing accents, doubled spaces).

### Stage 3: Locate context around matches

After detecting a candidate phrase:

- Resolve local context (nearest section header on same page).
- Resolve document context (backtrack to nearest entity header across pages).
- Attach both positions to output (entity page + evidence page).

This prevents false joins when a recommendation and a header are separated by page breaks.

### Stage 4: Enrich with OCR only when needed

Use OCR as fallback/enrichment for fields that are difficult in text layer:

- Typical use: title, authors, signatures, table cells in scanned pages.
- Keep OCR extraction small and scoped to one page/region at a time.
- Always keep a deterministic fallback value when OCR fails.

### Stage 5: Normalize and deduplicate

- Normalize to canonical labels only.
- Include a stable row key in append mode (for example year + file + evidence_page).
- Upsert on that key so reruns are idempotent.

### Stage 6: Verify with evidence

For each row, verify:

- Anchor header and entity are correct.
- Evidence phrase exists in the claimed evidence page.
- Normalized value matches wording.
- Rerun does not create duplicates.

## Adaptation Matrix For New Document Types

When reusing this method, keep the pipeline and swap only the domain-specific rules.

| Document type | Entity per row | Anchor examples | Detection examples | Normalization examples |
| --- | --- | --- | --- | --- |
| Kallelse (motions) | One motion recommendation | MOTION N, Styrelsens yttrande | yrkar styrelsen ... bifall/avslag | Tillstyrker, Avstyrker |
| Årsredovisning (financial notes) | One note line item | Not, Resultaträkning, Balansräkning | förändring uppgår till, avskrivning | Driftkostnad, Avskrivning, Räntekostnad |
| Stämmoprotokoll | One decision/vote | Beslut, Omröstning, Stämman beslutade | stämman beslutade att, bifölls, avslogs | Bifall, Avslag, Bordlagd |

## Script

Use:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py <YEAR> <PDF_FILE>
```

Append mode:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py <YEAR> <PDF_FILE> --append
```

Fast mode for text-layer PDFs (skip OCR fallback during resolution-page detection):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py <YEAR> <PDF_FILE> --append --no-ocr-fallback
```

Example:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2025 stamma2025.pdf --append
```

Batch example for multiple files:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2024 stamma-2024.pdf --append --no-ocr-fallback
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2023 stamma-2023.pdf --append --no-ocr-fallback
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_motion_resolutions.py 2022 stamma_kallelse_2022.pdf --append --no-ocr-fallback
```

Backfill coordinates for rows that already exist in data/motions.csv:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/backfill_motion_coordinates.py
```

Dry-run mode:

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/backfill_motion_coordinates.py --dry-run
```

## Verification checklist

1. Confirm title/page points to the right MOTION N header.
2. Confirm resolution_page contains explicit recommendation wording.
3. Confirm normalization is correct (Tillstyrker vs Avstyrker).
4. Re-run append mode and verify idempotency (no duplicate rows appended).
5. Confirm resolution_x/resolution_y/resolution_width/resolution_height points to the decision word on resolution_page.

## Limitations and future improvements

- Works best for text-selectable PDFs.
- For large backfills across many text PDFs, use --no-ocr-fallback to avoid expensive OCR scans on non-matching pages.
- OCR for title/authors is heuristic and may need manual correction for complex layouts.
- Resolution coordinates are based on text-layer word boxes from pdftotext -bbox-layout.
- When text-layer lookup misses, the script falls back to OCR word boxes (slower but broader coverage).
- For heavily degraded scans, some coordinates can still remain blank and require manual follow-up.
- A next step is to externalize patterns into per-document YAML files so one generic extractor can run multiple methods.
