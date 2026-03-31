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

## Board leadership extraction (pre-2013 backfill)

Dry-run board leadership extraction (writes artifacts under `extraction/artifacts/board_leadership/`):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py
```

Append board leadership rows to `data/general_states.csv` (categories 0, 1, 2, 3):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_board_leadership.py --append
```

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

Extract pre-2009 core states/events (2003, 2004, 2006, 2007, 2008):

```bash
/Users/aleksandr/code/sjotungan-analytics/.venv/bin/python extraction/scripts/extract_pre2009_states_events.py --append
```
