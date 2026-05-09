#!/usr/bin/env python3
"""
Generate a single LLM-friendly HTML file consolidating all stambyte sources
into one chronological timeline (newest first).

Reads the `stambyte` section from sources.yaml, downloads any missing files,
extracts text from each PDF/HTML, and emits text-stambyte.html. Sources marked
`split_by_date: true` are split into per-post entries based on date markers
inside the document.
"""

from __future__ import annotations

import html
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import pdfplumber
import yaml

from download_sources import download_file


OUTPUT_PATH = Path("text_stambyte.html")
SOURCES_YAML = Path("sources.yaml")
MIN_CHARS_PER_PAGE = 50  # below this, suspect a scanned PDF
DATE_RE = re.compile(r"^\s*(20\d{2}-\d{2}-\d{2})\s*$")
UNDATED_SORT_KEY = "0000-00-00"


class _VisibleTextExtractor(HTMLParser):
    """Strip HTML tags and pull out human-readable text."""

    SKIP_TAGS = {"script", "style", "head", "noscript", "meta", "link"}
    BLOCK_TAGS = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data):
        if self._skip_depth == 0:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = "".join(self._chunks)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def extract_html_text(local_path: Path) -> str:
    raw = local_path.read_text(encoding="utf-8", errors="replace")
    return _html_to_text(raw)


def _html_to_text(raw: str) -> str:
    parser = _VisibleTextExtractor()
    parser.feed(raw)
    return parser.get_text()


# Default separator/end-marker for the aktuellaArbeten-stambyte.html
# status page. Other pages (e.g. main news) override these in sources.yaml.
_DEFAULT_HR_PATTERN = r'<hr\s+class="bR10"\s*/?>'
_DEFAULT_END_MARKER = "<!--textEr_Styr SLUT-->"
# Date matchers — accept ASCII hyphen and Unicode en-dash; year-only or
# year-month variants are ignored as too imprecise.
_DATE_LINE_RE = re.compile(r"^\s*(20\d{2})[-–](\d{2})[-–](\d{2})\s*$", re.M)


def split_html_into_dated_posts(
    local_path: Path,
    separator_pattern: str = _DEFAULT_HR_PATTERN,
    end_marker: str = _DEFAULT_END_MARKER,
    filter_keyword: Optional[str] = None,
    filter_in_title: bool = False,
    skip_first_chunk: bool = True,
) -> list[tuple[Optional[str], str]]:
    """Split an HTML page into one chunk per separator (default <hr class="bR10"/>).

    Each chunk is text-extracted and tagged with the YYYY-MM-DD date found
    inside it (last date-only line — the post's footer stamp).

    skip_first_chunk: if True (default, used by the stambyte feed), the chunk
        before the first separator is treated as page chrome and dropped.
        If False (used by the main news page, which starts with the newest
        post directly), the first chunk is included.

    filter_keyword: if set, only chunks whose text contains it
        (case-insensitive) are returned.
    """
    raw = local_path.read_text(encoding="utf-8", errors="replace")
    if end_marker:
        end_idx = raw.find(end_marker)
        if end_idx > 0:
            raw = raw[:end_idx]

    sep_re = re.compile(separator_pattern, re.IGNORECASE)
    parts = sep_re.split(raw)
    if len(parts) < 2:
        return [(None, _html_to_text(raw))]

    candidate_parts = parts[1:] if skip_first_chunk else parts
    needle = filter_keyword.lower() if filter_keyword else None
    chunks: list[tuple[Optional[str], str]] = []
    for chunk_html in candidate_parts:
        text = _html_to_text(chunk_html)
        if not text:
            continue
        if needle:
            if filter_in_title:
                h2 = re.search(r"<h2[^>]*>([^<]+)</h2>", chunk_html, re.IGNORECASE)
                haystack = (h2.group(1) if h2 else "").lower()
            else:
                haystack = text.lower()
            if needle not in haystack:
                continue
        date_matches = list(_DATE_LINE_RE.finditer(text))
        if date_matches:
            y, m, d = date_matches[-1].groups()
            date = f"{y}-{m}-{d}"
            text_no_date = _DATE_LINE_RE.sub("", text).strip()
            chunks.append((date, text_no_date))
        else:
            chunks.append((None, text))
    return chunks


def extract_pdf_text(local_path: Path) -> tuple[str, bool]:
    """Return (combined_text, looks_scanned)."""
    pages: list[str] = []
    total_chars = 0
    with pdfplumber.open(str(local_path)) as pdf:
        page_count = len(pdf.pages)
        for i, page in enumerate(pdf.pages, start=1):
            try:
                text = (page.extract_text() or "").strip()
            except Exception as e:
                text = f"[Error extracting page {i}: {e}]"
            text = _collapse_doubled_lines(text)
            total_chars += len(text)
            pages.append(f"--- Page {i} ---\n{text}")
    looks_scanned = page_count > 0 and total_chars < page_count * MIN_CHARS_PER_PAGE
    return "\n\n".join(pages), looks_scanned


def _collapse_doubled_lines(text: str) -> str:
    """Undo the 'fake bold by drawing each glyph twice' trick used in some
    PDFs (e.g. Portinfo headers render 'BBrrff SSjjööttuunnggaann' instead of
    'Brf Sjötungan'). Only collapses lines whose entire visible content is
    character-doubled, leaving normal text alone.
    """
    return "\n".join(_maybe_collapse(line) for line in text.splitlines())


def _maybe_collapse(line: str) -> str:
    visible = [c for c in line if not c.isspace()]
    if len(visible) < 4 or len(visible) % 2 != 0:
        return line
    if len(set(visible)) == 1:
        # Signature/divider lines like '____...' or '----...' satisfy the
        # all-pairs-match check trivially but aren't doubled glyphs.
        return line
    if not all(visible[i] == visible[i + 1] for i in range(0, len(visible), 2)):
        return line
    out: list[str] = []
    i = 0
    while i < len(line):
        ch = line[i]
        if ch.isspace():
            out.append(ch)
            i += 1
        else:
            out.append(ch)
            i += 2
    return "".join(out)


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[-\s]+", "-", s)
    return s[:80] or "section"


def ensure_local(item: dict) -> Optional[Path]:
    local_path = Path(item["local_path"])
    # Sources marked split_by_date are feed-like pages (e.g. status pages
    # that accumulate posts), so always re-fetch to capture the latest state.
    if item.get("split_by_date") and local_path.exists():
        local_path.unlink()
    if local_path.exists():
        return local_path
    url = item.get("url")
    if not url:
        return None
    if download_file(url, str(local_path)):
        return local_path
    return None


def extract_image_text(local_path: Path) -> str:
    """OCR a screenshot/photo. Swedish-optimised."""
    import pytesseract
    from PIL import Image

    return pytesseract.image_to_string(Image.open(local_path), lang="swe").strip()


def build_entries(items: list[dict]) -> list[dict]:
    """Convert raw sources.yaml items into a flat list of timeline entries."""
    entries: list[dict] = []
    for item in items:
        title = item.get("title") or item["local_path"]
        print(f"Processing: {title}")
        local_path = ensure_local(item)
        if local_path is None:
            print(f"  ❌ Could not obtain {item['local_path']}; skipping")
            continue

        ext = local_path.suffix.lower()
        looks_scanned = False

        meta_base = {
            "source_title": title,
            "source_url": item.get("url") or "",
            "source_local_path": item["local_path"],
            "source_type": item.get("source_type") or "",
            "looks_scanned": looks_scanned,
        }

        if item.get("split_by_date"):
            if ext not in {".html", ".htm"}:
                print(f"  ⚠️  split_by_date is only supported for HTML; got {ext}")
            split_kwargs = {}
            if "separator_pattern" in item:
                split_kwargs["separator_pattern"] = item["separator_pattern"]
            if "end_marker" in item:
                split_kwargs["end_marker"] = item["end_marker"]
            if "filter_keyword" in item:
                split_kwargs["filter_keyword"] = item["filter_keyword"]
            if "filter_in_title" in item:
                split_kwargs["filter_in_title"] = item["filter_in_title"]
            if "skip_first_chunk" in item:
                split_kwargs["skip_first_chunk"] = item["skip_first_chunk"]
            chunks = split_html_into_dated_posts(local_path, **split_kwargs)
            dated = [(d, c) for d, c in chunks if d]
            undated_chunks = [c for d, c in chunks if not d]
            for i, (date, chunk) in enumerate(dated, start=1):
                entries.append({**meta_base, "date": date, "text": chunk, "post_index": i})
            if undated_chunks:
                entries.append({
                    **meta_base,
                    "date": None,
                    "text": "\n\n---\n\n".join(undated_chunks),
                    "post_index": 0,
                    "note": "Static / undated content from the same page (intro, contact info).",
                })
            print(f"  ✂️  Split into {len(dated)} dated post(s)" + (f" + {len(undated_chunks)} undated chunk(s)" if undated_chunks else ""))
            continue

        if ext == ".pdf":
            text, looks_scanned = extract_pdf_text(local_path)
            meta_base["looks_scanned"] = looks_scanned
            if looks_scanned:
                print("  ⚠️  Low text density — likely scanned. Output included as-is.")
        elif ext in {".html", ".htm"}:
            text = extract_html_text(local_path)
        elif ext in {".png", ".jpg", ".jpeg", ".webp"}:
            text = extract_image_text(local_path)
            print(f"  🔤 OCR'd ({len(text)} chars)")
        else:
            print(f"  ⚠️  Unknown extension {ext}; reading as plain text")
            text = local_path.read_text(encoding="utf-8", errors="replace")

        entry = {**meta_base, "date": item.get("document_date"), "text": text.strip(), "post_index": None}
        if item.get("note"):
            entry["note"] = item["note"]
        entries.append(entry)
        if not item.get("document_date"):
            print("  ⚠️  No document_date set — entry will be marked undated")
    return entries


def render_html(entries: list[dict]) -> str:
    # Sort newest-first; undated entries sink to the bottom.
    entries_sorted = sorted(
        entries,
        key=lambda e: (e["date"] or UNDATED_SORT_KEY),
        reverse=True,
    )

    style = (
        "body{font-family:system-ui,sans-serif;max-width:900px;margin:2em auto;"
        "padding:0 1em;line-height:1.4}"
        "pre{white-space:pre-wrap;word-wrap:break-word;background:#f7f7f7;"
        "padding:1em;border-radius:4px;font-size:13px}"
        "article{margin:2em 0;padding-top:1em;border-top:2px solid #333}"
        "article h2{margin:0 0 0.3em;font-size:1.2em}"
        "time{font-family:ui-monospace,monospace;background:#222;color:#fff;"
        "padding:2px 8px;border-radius:3px;font-size:0.95em}"
        ".stype{display:inline-block;background:#e6efff;color:#234;"
        "border:1px solid #b8cdee;padding:1px 7px;border-radius:3px;"
        "font-size:0.85em;margin:0 0.4em}"
        ".source-meta{color:#666;font-size:0.9em;margin:0.4em 0 1em}"
        ".toc{column-count:2;column-gap:2em}"
        ".toc li{margin:0.15em 0;break-inside:avoid}"
        ".warn{color:#a40000}"
        ".undated{color:#999}"
    )

    parts = [
        "<!DOCTYPE html>",
        '<html lang="sv">',
        "<head>",
        '<meta charset="utf-8">',
        "<title>Stambyte – kronologisk sammanställning</title>",
        f"<style>{style}</style>",
        "</head>",
        "<body>",
        "<h1>Stambyte – kronologisk sammanställning</h1>",
        "<section>",
        "<p>This page consolidates the full timeline of communication "
        "around the stambyte (pipe replacement) project at BRF Sjötungan, "
        "drawing from multiple channels into a single chronological feed "
        "(newest first). The goal is to make the project history easy to "
        "follow at a glance and convenient for LLMs to consume.</p>",
        "<p>Sources combined:</p>",
        "<ol>",
        "<li>Extra stämma — kallelse and protokoll for the extraordinary "
        "general meeting where the stambyte was formally decided.</li>",
        "<li>Web site main news — announcements posted to the front page "
        "of sjotungan.se.</li>",
        "<li>Web site &ldquo;port information&rdquo; — digital archive of "
        "the Portinfo bulletins published online.</li>",
        "<li>Web site stambyte feed — the running status page at "
        "<code>aktuellt/aktuellaArbeten-stambyte.html</code>, split here "
        "into one entry per dated post.</li>",
        "<li>Printed port information — the physical Portinfo bulletins "
        "posted at building entrances (same content as the digital "
        "archive, surfaced for readers who only see the printed copy).</li>",
        "</ol>",
        f"<p class=\"source-meta\">Auto-generated from <code>sources.yaml</code> "
        f"by <code>generate_stambyte_text.py</code> · "
        f"{len(entries_sorted)} entries.</p>",
        "</section>",
        "<h2>Innehåll</h2>",
        '<ul class="toc">',
    ]
    for i, e in enumerate(entries_sorted):
        anchor = f"e{i:03d}"
        e["_anchor"] = anchor
        date_label = e["date"] or "odaterat"
        title_label = e["source_title"]
        if e.get("post_index"):
            title_label = f"{title_label} (post {e['post_index']})"
        type_label = (
            f' <span class="stype">{html.escape(e["source_type"])}</span>'
            if e.get("source_type")
            else ""
        )
        parts.append(
            f'<li><a href="#{anchor}"><time>{html.escape(date_label)}</time>'
            f"{type_label} {html.escape(title_label)}</a></li>"
        )
    parts.append("</ul>")

    for e in entries_sorted:
        date_html = (
            f"<time>{html.escape(e['date'])}</time>"
            if e["date"]
            else '<time class="undated">odaterat</time>'
        )
        post_suffix = f" (post {e['post_index']})" if e.get("post_index") else ""
        warn = (
            ' <span class="warn">— WARNING: low extracted text density, '
            "likely scanned (OCR needed).</span>"
            if e["looks_scanned"]
            else ""
        )
        note = (
            f"<br><em>{html.escape(e['note'])}</em>"
            if e.get("note")
            else ""
        )
        source_line = (
            f'Source: <a href="{html.escape(e["source_url"])}">{html.escape(e["source_url"])}</a>'
            if e["source_url"]
            else "Source: <em>local-only (no URL)</em>"
        )
        type_badge = (
            f' <span class="stype">{html.escape(e["source_type"])}</span> &middot;'
            if e.get("source_type")
            else " &middot;"
        )
        parts.extend(
            [
                f'<article id="{e["_anchor"]}">',
                f"<h2>{date_html}{type_badge} {html.escape(e['source_title'])}{html.escape(post_suffix)}</h2>",
                f'<p class="source-meta">{source_line}'
                f"<br>Local: <code>{html.escape(e['source_local_path'])}</code>{warn}{note}</p>",
                f"<pre>{html.escape(e['text'])}</pre>",
                "</article>",
            ]
        )
    parts.append("</body></html>")
    return "\n".join(parts)


def main() -> int:
    sources = yaml.safe_load(SOURCES_YAML.read_text())
    items = sources.get("stambyte") or []
    if not items:
        print("No `stambyte:` section found in sources.yaml", file=sys.stderr)
        return 1

    entries = build_entries(items)
    out = render_html(entries)
    OUTPUT_PATH.write_text(out, encoding="utf-8")
    dated = sum(1 for e in entries if e["date"])
    print(f"\n✅ Wrote {OUTPUT_PATH} ({len(entries)} entries, {dated} dated, {len(out):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
