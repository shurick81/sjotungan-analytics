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
            dated.sort(key=lambda dc: dc[0])
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


PAGE_TEMPLATE = """<!DOCTYPE html>
<!--
    AUTO-GENERATED — do not edit by hand.
    Regenerate with: python3 generate_text_stambyte.py
    Source data:     sources.yaml (stambyte section)
    Generator:       generate_text_stambyte.py
-->
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="generator" content="generate_text_stambyte.py">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stambyte – kronologisk sammanställning</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        :root {{
            --vm-blue: #1f3f95;
            --vm-card: #e8edf6;
            --vm-text: #2f3d52;
            --vm-muted: #5e6b80;
            --vm-border: #c9d6ec;
        }}

        body {{
            font-size: 1rem;
            background: #f0f4fa;
            color: var(--vm-text);
        }}

        @media (max-width: 640px) {{
            body {{ font-size: 0.875rem; }}
            h1 {{ font-size: 1.5rem !important; }}
        }}

        h1 {{ font-size: 2.25rem; }}

        .source-list {{
            list-style: decimal;
            padding-left: 1.4em;
            margin: 0;
        }}
        .source-list li {{
            margin: 0.3em 0;
        }}
        .source-meta {{
            color: var(--vm-muted);
            font-size: 0.9em;
            margin: 0.4em 0 1em;
        }}

        .toc {{
            column-count: 2;
            column-gap: 2em;
            list-style: none;
            padding: 0;
            margin: 0;
        }}
        .toc li {{
            margin: 0.2em 0;
            break-inside: avoid;
        }}
        .toc a {{
            color: var(--vm-blue);
            text-decoration: none;
        }}
        .toc a:hover {{
            text-decoration: underline;
        }}

        article {{
            margin: 1.5em 0;
            padding-top: 1em;
            border-top: 2px solid var(--vm-border);
        }}
        article h2 {{
            margin: 0 0 0.4em;
            font-size: 1.15em;
            color: var(--vm-blue);
        }}

        time {{
            font-family: ui-monospace, monospace;
            background: var(--vm-blue);
            color: #fff;
            padding: 2px 8px;
            border-radius: 3px;
            font-size: 0.95em;
        }}
        time.undated {{
            background: #888;
        }}

        .stype {{
            display: inline-block;
            background: var(--vm-card);
            color: var(--vm-blue);
            border: 1px solid var(--vm-border);
            padding: 1px 7px;
            border-radius: 3px;
            font-size: 0.85em;
            margin: 0 0.4em;
        }}

        pre {{
            white-space: pre-wrap;
            word-wrap: break-word;
            background: #f7f9fd;
            border: 1px solid var(--vm-border);
            padding: 1em;
            border-radius: 8px;
            font-size: 13px;
        }}

        .doc-url {{
            color: var(--vm-blue);
            text-decoration: none;
            word-break: break-all;
        }}
        .doc-url:hover {{
            text-decoration: underline;
        }}

        .warn {{ color: #a40000; }}
    </style>
</head>
<body class="bg-gray-100 text-gray-800">
    <header class="bg-blue-900 text-white sticky top-0 z-50 shadow">
        <nav class="container mx-auto flex flex-wrap justify-center space-x-4 md:space-x-8 py-4">
            <button onclick="location.href='https://visionmyggan.se/#start'" class="hover:text-yellow-400 font-semibold">Start</button>
            <button onclick="location.href='https://visionmyggan.se/#vision'" class="hover:text-yellow-400 font-semibold">Vision</button>
            <button onclick="location.href='https://visionmyggan.se/#motioner'" class="hover:text-yellow-400 font-semibold">Motioner</button>
            <button onclick="location.href='https://visionmyggan.se/#forum'" class="hover:text-yellow-400 font-semibold">Forum</button>
        </nav>
    </header>

    <main class="container mx-auto mt-8 p-4">
        <section class="bg-gradient-to-r from-blue-50 via-white to-blue-50 p-6 rounded-2xl shadow-xl max-w-6xl mx-auto scroll-mt-24">
            <h1 class="text-blue-900 font-extrabold text-3xl mb-2 border-b-4 border-blue-300 pb-2">Stambyte – kronologisk sammanställning</h1>
            <p class="mb-6" style="color: var(--vm-muted);">
                Hela tidslinjen för kommunikation kring stambytet i BRF Sjötungan, från flera olika kanaler samlade till ett kronologiskt flöde (nyaste först). Syftet är att göra projektets historik lätt att överblicka och bekvämt för språkmodeller (LLM) att läsa.
            </p>
{content_html}
        </section>
    </main>
</body>
</html>
"""


def render_html(entries: list[dict]) -> str:
    # Sort newest-first; undated entries sink to the bottom.
    entries_sorted = sorted(
        entries,
        key=lambda e: (e["date"] or UNDATED_SORT_KEY),
        reverse=True,
    )
    for i, e in enumerate(entries_sorted):
        e["_anchor"] = f"e{i:03d}"

    parts: list[str] = []

    parts.append('<section class="mb-10" aria-labelledby="sources-heading">')
    parts.append('<h2 id="sources-heading" class="text-xl font-bold text-blue-900 mb-3">Sammanställda källor</h2>')
    parts.append('<ol class="source-list text-sm">')
    parts.append('<li>Extra stämma — kallelse och protokoll från den extra föreningsstämma där stambytet formellt beslutades.</li>')
    parts.append('<li>Webbplatsens nyheter — inlägg publicerade på <em>Aktuell information</em> på sjotungan.se.</li>')
    parts.append('<li>Webbplatsens portinformation — det digitala arkivet av Portinfo-bladen som publiceras på webbplatsen.</li>')
    parts.append('<li>Webbplatsens stambyte-flöde — den löpande statussidan <code>aktuellt/aktuellaArbeten-stambyte.html</code>, här uppdelad i ett inlägg per daterad post.</li>')
    parts.append('<li>Tryckt portinformation — de fysiska Portinfo-bladen som sätts upp i porten (samma innehåll som det digitala arkivet, medtaget för läsare som bara ser den tryckta versionen).</li>')
    parts.append('</ol>')
    parts.append(
        f'<p class="source-meta mt-3">Automatiskt genererad från '
        f'<code>sources.yaml</code> av <code>generate_text_stambyte.py</code> · '
        f'{len(entries_sorted)} poster.</p>'
    )
    parts.append('</section>')

    parts.append('<section class="mb-10" aria-labelledby="toc-heading">')
    parts.append('<h2 id="toc-heading" class="text-xl font-bold text-blue-900 mb-3">Innehåll</h2>')
    parts.append('<ul class="toc">')
    for e in entries_sorted:
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
            f'<li><a href="#{e["_anchor"]}"><time>{html.escape(date_label)}</time>'
            f"{type_label} {html.escape(title_label)}</a></li>"
        )
    parts.append('</ul>')
    parts.append('</section>')

    parts.append('<section aria-labelledby="entries-heading">')
    parts.append('<h2 id="entries-heading" class="text-xl font-bold text-blue-900 mb-3">Tidslinje</h2>')
    for e in entries_sorted:
        date_html = (
            f"<time>{html.escape(e['date'])}</time>"
            if e["date"]
            else '<time class="undated">odaterat</time>'
        )
        post_suffix = f" (post {e['post_index']})" if e.get("post_index") else ""
        warn = (
            ' <span class="warn">— VARNING: låg textextraktion, '
            "troligen inskannad (OCR krävs).</span>"
            if e["looks_scanned"]
            else ""
        )
        note = (
            f"<br><em>{html.escape(e['note'])}</em>"
            if e.get("note")
            else ""
        )
        source_line = (
            f'Källa: <a class="doc-url" href="{html.escape(e["source_url"])}">{html.escape(e["source_url"])}</a>'
            if e["source_url"]
            else "Källa: <em>endast lokal (ingen URL)</em>"
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
                f"<br>Lokalt: <code>{html.escape(e['source_local_path'])}</code>{warn}{note}</p>",
                f"<pre>{html.escape(e['text'])}</pre>",
                "</article>",
            ]
        )
    parts.append('</section>')

    content_html = "\n".join(parts)
    return PAGE_TEMPLATE.format(content_html=content_html)


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
