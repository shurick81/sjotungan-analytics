# Guide HTML ↔ PDF Comparison Method

## Goal

Verify that `brf-economy-guide.html` and `brf-economy-guide.pdf` contain the
same text in the same order, and that all illustrations/charts present in the
PDF have corresponding chart containers or caption text in the HTML.

## Scope

- **Text comparison**: every paragraph in the PDF should appear in the HTML in
  the same relative order.  The comparison uses fuzzy matching (≥ 55%)
  because wording may vary slightly between the interactive HTML version and
  the static PDF version.
- **Illustration / chart captions**: each chart caption line in the PDF (lines
  containing "Källa:" that describe a chart) should have a matching caption or
  chart container `<div>` in the HTML.
- **Illustration content** (chart data / images): out of scope — chart data is
  generated dynamically from CSV in the HTML and rendered as static images in
  the PDF; verifying pixel-level fidelity is a separate concern.

## How it works

### 1. PDF text extraction

- Uses `pdftotext -layout` to extract text.
- Strips boilerplate: page headers ("BRF Sjötungan — Förstå din förenings
  ekonomi"), footers ("Framtagen av medlemmar, för medlemmar"), page numbers
  ("Sida N"), QR-code instructions ("Skanna QR-koden …"), and
  self-referential URLs (visionmyggan.se/…/brf-economy-guide.{html,pdf}).
- Merges continuation lines into paragraphs (blank-line separated).
- Filters out fragments shorter than 15 characters.

### 2. HTML text extraction

- Parses HTML with Python's `html.parser` (no external dependency).
- Skips `<script>`, `<style>`, `<noscript>` tags.
- Flushes text at block-level boundaries (`<p>`, `<h*>`, `<div>`, `<li>`, …).
- Collects chart `<div>` IDs (containing "chart"/"Chart" in the id attribute).
- Collects caption texts (paragraphs containing "Källa:").

### 3. Paragraph order comparison

For each PDF paragraph, the script finds the best-matching HTML paragraph by
`difflib.SequenceMatcher` similarity ratio.  It then checks:

- **MISSING**: similarity < 55% — paragraph not found in HTML.
- **ORDER**: the matched HTML index is before a previously matched index —
  content appears in a different order than the PDF.

### 4. Caption comparison

Each PDF caption line (containing "Källa:" and > 30 chars) is matched against
HTML caption texts with a 45% similarity threshold.

## Output

A plain-text report with:
- Summary counts (paragraphs, chart divs, matches, issues).
- List of MISSING paragraphs.
- List of ORDER violations.
- List of MISSING captions.
- HTML chart container div IDs.
- Overall verdict.

Exit code: 0 = no issues, 1 = issues found.

## Usage

```bash
# Dry-run (report to stdout)
python extraction/scripts/compare_guide_html_pdf.py

# Save report to file
python extraction/scripts/compare_guide_html_pdf.py -o /tmp/guide_diff.txt

# Verbose: show all paragraph alignments
python extraction/scripts/compare_guide_html_pdf.py -v

# Custom paths
python extraction/scripts/compare_guide_html_pdf.py --html path/to.html --pdf path/to.pdf
```

## When to run

- After editing `brf-economy-guide.html` (added/removed/reordered sections).
- After regenerating `brf-economy-guide.pdf` from the HTML.
- Before committing changes to either file, to ensure they stay in sync.

## Interpreting results

| Status | Meaning | Action |
|--------|---------|--------|
| ok | Paragraph matched at ≥ 55% in correct order | None |
| MISSING | PDF paragraph not found in HTML | Add the content to HTML, or verify it was intentionally omitted |
| ORDER | HTML paragraph is out of PDF order | Reorder the HTML section or update the PDF |
| Caption MISSING | Chart caption from PDF not in HTML | Add a `source-ref` paragraph or chart div |

## Limitations

- Fuzzy matching may produce false positives on short, generic phrases.
- Tables are flattened to cell text; table structure is not compared.
- Chart image content is not verified (only caption text presence).
- QR codes and decorative images in the PDF are ignored.
