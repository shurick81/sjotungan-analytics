#!/usr/bin/env python3
"""
Compare brf-economy-guide.html against brf-economy-guide.pdf.

Checks:
  1. Text paragraphs appear in the same order (fuzzy matching).
  2. Illustration / chart captions from PDF have corresponding
     chart containers or caption text in the HTML.

Usage:
    # Dry-run (prints diff report to stdout)
    python extraction/scripts/compare_guide_html_pdf.py

    # Write report to a file
    python extraction/scripts/compare_guide_html_pdf.py -o /tmp/guide_diff.txt
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML_PATH = ROOT / "brf-economy-guide.html"
PDF_PATH = ROOT / "brf-economy-guide.pdf"

# ── Helpers ───────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Collapse whitespace, strip decorations, normalize unicode."""
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Remove zero-width chars
    text = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", text)
    return text


def strip_boilerplate(text: str) -> str:
    """Remove PDF page headers/footers that won't appear in the HTML."""
    text = re.sub(
        r"BRF Sjötungan — Förstå din förenings ekonomi", "", text
    )
    text = re.sub(r"Framtagen av medlemmar, för medlemmar", "", text)
    text = re.sub(r"Sida \d+", "", text)
    # QR-code descriptive text and self-referential URLs (PDF-only artefacts)
    text = re.sub(r"Skanna QR-koden eller besök adressen i din webbläsare", "", text)
    text = re.sub(r"https?://visionmyggan\.se/sjotungan-analytics/brf-economy-guide\.(?:html|pdf)", "", text)
    return text


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


# ── PDF text extraction ──────────────────────────────────────────────

def extract_pdf_paragraphs(pdf_path: Path) -> list[str]:
    """Extract merged paragraphs from PDF using pdftotext."""
    raw = subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True,
        errors="replace",
    )
    raw = strip_boilerplate(raw)
    lines = raw.split("\n")

    # Merge continuation lines into paragraphs
    paragraphs: list[str] = []
    buf = ""
    for line in lines:
        s = line.strip()
        if not s:
            if buf:
                paragraphs.append(normalize(buf))
                buf = ""
        else:
            buf = (buf + " " + s) if buf else s
    if buf:
        paragraphs.append(normalize(buf))

    # Filter out very short fragments (< 15 chars: page nums, stray tokens)
    paragraphs = [p for p in paragraphs if len(p) >= 15]
    return paragraphs


def extract_pdf_captions(pdf_path: Path) -> list[str]:
    """Extract illustration caption lines from the PDF.

    Captions are lines that contain "Källa:" and describe a chart
    (they typically mention 'per kvm', 'för BRF Sjötungan', years, etc.)
    """
    raw = subprocess.check_output(
        ["pdftotext", "-layout", str(pdf_path), "-"],
        text=True,
        errors="replace",
    )
    raw = strip_boilerplate(raw)
    lines = raw.split("\n")

    # Merge continuation lines
    merged: list[str] = []
    buf = ""
    for line in lines:
        s = line.strip()
        if not s:
            if buf:
                merged.append(normalize(buf))
                buf = ""
        else:
            buf = (buf + " " + s) if buf else s
    if buf:
        merged.append(normalize(buf))

    captions = []
    for line in merged:
        if "Källa:" in line and len(line) > 30:
            captions.append(line)
    return captions


# ── HTML text extraction ─────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Extract visible text and chart div IDs from HTML."""

    SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self):
        super().__init__()
        self.paragraphs: list[str] = []
        self.chart_ids: list[str] = []
        self.caption_texts: list[str] = []
        self._skip_depth = 0
        self._buf = ""

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)

        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
            return

        # Track chart container divs
        div_id = ad.get("id", "")
        if tag == "div" and ("chart" in div_id.lower() or "Chart" in div_id):
            self.chart_ids.append(div_id)

        # Block-level tags flush the buffer
        if tag in (
            "p", "h1", "h2", "h3", "h4", "h5", "h6",
            "div", "li", "tr", "td", "th", "br", "hr",
            "blockquote", "section", "article", "header",
        ):
            self._flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in ("p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "div"):
            self._flush()

    def handle_data(self, data):
        if self._skip_depth:
            return
        self._buf += data

    def _flush(self):
        t = normalize(self._buf)
        self._buf = ""
        if len(t) >= 10:
            self.paragraphs.append(t)
            # Track caption texts (contain "Källa:")
            if "Källa:" in t:
                self.caption_texts.append(t)


def extract_html_content(html_path: Path):
    """Return (paragraphs, chart_ids, caption_texts) from HTML."""
    text = html_path.read_text(encoding="utf-8")
    parser = _TextExtractor()
    parser.feed(text)
    parser._flush()
    return parser.paragraphs, parser.chart_ids, parser.caption_texts


# ── Comparison logic ─────────────────────────────────────────────────

SIMILARITY_THRESHOLD = 0.55  # fuzzy match threshold


def compare_text_order(
    pdf_paras: list[str], html_paras: list[str]
) -> list[dict]:
    """Compare paragraph ordering between PDF and HTML.

    For each PDF paragraph, find the best-matching HTML paragraph.
    Report ordering violations (PDF paragraph N maps to HTML index
    that is before a previous match).
    """
    results: list[dict] = []
    last_html_idx = -1

    for pi, pp in enumerate(pdf_paras):
        best_sim = 0.0
        best_hi = -1
        for hi, hp in enumerate(html_paras):
            s = similarity(pp, hp)
            if s > best_sim:
                best_sim = s
                best_hi = hi

        status = "ok"
        if best_sim < SIMILARITY_THRESHOLD:
            status = "MISSING"
        elif best_hi < last_html_idx:
            status = "ORDER"

        results.append(
            {
                "pdf_idx": pi,
                "html_idx": best_hi,
                "similarity": best_sim,
                "status": status,
                "pdf_text": pp[:100],
                "html_text": (
                    html_paras[best_hi][:100] if best_hi >= 0 else ""
                ),
            }
        )

        if best_sim >= SIMILARITY_THRESHOLD:
            last_html_idx = max(last_html_idx, best_hi)

    return results


def compare_captions(
    pdf_captions: list[str],
    html_caption_texts: list[str],
    html_chart_ids: list[str],
) -> list[dict]:
    """Check that each PDF chart caption has a match in HTML."""
    results = []
    for ci, cap in enumerate(pdf_captions):
        best_sim = 0.0
        best_match = ""
        for hc in html_caption_texts:
            s = similarity(cap, hc)
            if s > best_sim:
                best_sim = s
                best_match = hc

        status = "ok" if best_sim >= 0.45 else "MISSING"
        results.append(
            {
                "pdf_caption": cap[:120],
                "best_html_match": best_match[:120],
                "similarity": best_sim,
                "status": status,
            }
        )
    return results


# ── Report ────────────────────────────────────────────────────────────

def print_report(
    text_results: list[dict],
    caption_results: list[dict],
    html_chart_ids: list[str],
    pdf_captions: list[str],
    pdf_paras: list[str],
    html_paras: list[str],
    out=None,
):
    out = out or sys.stdout

    def w(s=""):
        print(s, file=out)

    w("=" * 70)
    w("  brf-economy-guide: HTML ↔ PDF comparison report")
    w("=" * 70)

    # Summary counts
    missing_text = [r for r in text_results if r["status"] == "MISSING"]
    order_issues = [r for r in text_results if r["status"] == "ORDER"]
    missing_caps = [r for r in caption_results if r["status"] == "MISSING"]

    w()
    w(f"PDF paragraphs:      {len(pdf_paras)}")
    w(f"HTML paragraphs:     {len(html_paras)}")
    w(f"PDF caption lines:   {len(pdf_captions)}")
    w(f"HTML chart divs:     {len(html_chart_ids)}")
    w()
    w(f"Text matches:        {len(text_results) - len(missing_text) - len(order_issues)}")
    w(f"Text MISSING in HTML:{len(missing_text)}")
    w(f"Text ORDER issues:   {len(order_issues)}")
    w(f"Caption MISSING:     {len(missing_caps)}")
    w()

    # ── Missing text ──
    if missing_text:
        w("-" * 70)
        w("TEXT MISSING IN HTML (PDF paragraph not found in HTML)")
        w("-" * 70)
        for r in missing_text:
            w(f"  [{r['pdf_idx']:3d}] sim={r['similarity']:.2f}  {r['pdf_text']}")
        w()

    # ── Order issues ──
    if order_issues:
        w("-" * 70)
        w("TEXT ORDER ISSUES (paragraph appears out of PDF order in HTML)")
        w("-" * 70)
        for r in order_issues:
            w(
                f"  PDF[{r['pdf_idx']:3d}] → HTML[{r['html_idx']:3d}]"
                f"  sim={r['similarity']:.2f}"
            )
            w(f"    PDF:  {r['pdf_text']}")
            w(f"    HTML: {r['html_text']}")
        w()

    # ── Missing captions ──
    if missing_caps:
        w("-" * 70)
        w("CHART CAPTIONS MISSING IN HTML")
        w("-" * 70)
        for r in missing_caps:
            w(f"  sim={r['similarity']:.2f}  PDF: {r['pdf_caption']}")
            if r["best_html_match"]:
                w(f"             best: {r['best_html_match']}")
        w()

    # ── Chart divs ──
    w("-" * 70)
    w("HTML chart container divs")
    w("-" * 70)
    for cid in html_chart_ids:
        w(f"  <div id=\"{cid}\">")
    w()

    # ── Overall verdict ──
    issues = len(missing_text) + len(order_issues) + len(missing_caps)
    if issues == 0:
        w("✓ No issues found — text and captions match between PDF and HTML.")
    else:
        w(f"⚠ {issues} issue(s) found — review details above.")

    w()
    return issues


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compare brf-economy-guide HTML vs PDF content"
    )
    parser.add_argument(
        "--html",
        type=Path,
        default=HTML_PATH,
        help="Path to HTML file",
    )
    parser.add_argument(
        "--pdf",
        type=Path,
        default=PDF_PATH,
        help="Path to PDF file",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write report to file instead of stdout",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show all paragraph matches, not just problems",
    )
    args = parser.parse_args()

    if not args.html.exists():
        sys.exit(f"HTML not found: {args.html}")
    if not args.pdf.exists():
        sys.exit(f"PDF not found: {args.pdf}")

    # Extract
    pdf_paras = extract_pdf_paragraphs(args.pdf)
    pdf_captions = extract_pdf_captions(args.pdf)
    html_paras, html_chart_ids, html_caption_texts = extract_html_content(
        args.html
    )

    # Compare
    text_results = compare_text_order(pdf_paras, html_paras)
    caption_results = compare_captions(
        pdf_captions, html_caption_texts, html_chart_ids
    )

    # Report
    out_file = None
    if args.output:
        out_file = open(args.output, "w", encoding="utf-8")

    issues = print_report(
        text_results,
        caption_results,
        html_chart_ids,
        pdf_captions,
        pdf_paras,
        html_paras,
        out=out_file,
    )

    if args.verbose:
        target = out_file or sys.stdout
        print("\n" + "=" * 70, file=target)
        print("  FULL PARAGRAPH ALIGNMENT (verbose)", file=target)
        print("=" * 70, file=target)
        for r in text_results:
            flag = " " if r["status"] == "ok" else r["status"]
            print(
                f"  {flag:7s} PDF[{r['pdf_idx']:3d}]→HTML[{r['html_idx']:3d}]"
                f"  sim={r['similarity']:.2f}  {r['pdf_text'][:80]}",
                file=target,
            )

    if out_file:
        out_file.close()
        print(f"Report written to {args.output}")

    sys.exit(1 if issues > 0 else 0)


if __name__ == "__main__":
    main()
