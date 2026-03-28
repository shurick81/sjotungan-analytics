# Extraction Workspace

This folder groups reusable data-extraction assets in one place.

## Structure

- `scripts/` — executable extraction scripts.
- `methods/` — methodology documents, assumptions, and verification steps.

## Current assets

- Script: `scripts/extract_motion_resolutions.py`
- Method doc: `methods/motion_resolutions.md`

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
