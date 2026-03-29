# Extraction Workspace

This folder groups reusable data-extraction assets in one place.

## Structure

- `scripts/` — executable extraction scripts.
- `methods/` — methodology documents, assumptions, and verification steps.

## Current assets

- Script: `scripts/extract_motion_resolutions.py`
- Method doc: `methods/motion_resolutions.md`
- Script: `scripts/extract_stamma_attendance.py`
- Method doc: `methods/stamma_attendance.md`

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
