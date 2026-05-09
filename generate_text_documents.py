#!/usr/bin/env python3
"""
Generate a fully-static text_documents.html from sources.yaml.

The previous version of this page resolved its annual-report and
stamma-protocol lists at runtime via fetch() + js-yaml, which means an
agent fetching the page with curl gets only loading placeholders. This
generator produces the same page with all lists baked into the HTML, so
one curl request returns the full content.

Mirrors the runtime behaviour the page used to have:
  - annual_reports → linked via `text_local_path` from sources.yaml,
    sorted by finance_period descending.
  - stamma_protocols → for each entry, look next to `local_path` for
    a sibling `*_text.txt` first, then `*_ocr.txt`. Sorted by
    meeting_year desc, then basename. Filenames containing "extra"
    get an "extra" pill.
"""

from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import yaml


SOURCES_YAML = Path("sources.yaml")
OUTPUT_PATH = Path("text_documents.html")


def derive_txt_path(pdf_local_path: str) -> str | None:
    """Return the first existing sibling text file (_text.txt, _ocr.txt)."""
    if not pdf_local_path:
        return None
    base = re.sub(r"\.pdf$", "", pdf_local_path, flags=re.IGNORECASE)
    for suffix in ("_text.txt", "_ocr.txt"):
        candidate = f"{base}{suffix}"
        if Path(candidate).exists():
            return candidate
    return None


def render_annual_reports(reports: list[dict]) -> str:
    if not reports:
        return (
            '<div class="empty-state mt-4 p-4 text-sm">'
            "Inga årsredovisningar hittades i sources.yaml."
            "</div>"
        )
    sorted_reports = sorted(
        reports, key=lambda r: r.get("finance_period") or 0, reverse=True
    )
    items = []
    for r in sorted_reports:
        year = r.get("finance_period")
        year_label = html.escape(str(year)) if year is not None else "–"
        url = r.get("text_local_path")
        if url:
            url_e = html.escape(url)
            cell = (
                f'<a class="doc-url" href="{url_e}" target="_blank" '
                f'rel="noopener">{url_e}</a>'
            )
        else:
            cell = '<span class="doc-missing">saknas</span>'
        items.append(
            f"<li>"
            f'<span class="doc-year">{year_label}</span>'
            f"{cell}"
            f"</li>"
        )
    return '<ul class="doc-list">' + "".join(items) + "</ul>"


def render_stamma_protocols(protocols: list[dict]) -> str:
    if not protocols:
        return (
            '<div class="empty-state mt-4 p-4 text-sm">'
            "Inga stämmoprotokoll hittades i sources.yaml."
            "</div>"
        )

    def sort_key(p: dict) -> tuple[int, str]:
        year = -(p.get("meeting_year") or 0)
        name = Path(p.get("local_path") or "").name
        return (year, name)

    sorted_protocols = sorted(protocols, key=sort_key)
    items = []
    for p in sorted_protocols:
        year = p.get("meeting_year")
        year_label = html.escape(str(year)) if year is not None else "–"
        filename = Path(p.get("local_path") or "").name
        is_extra = bool(re.search(r"extra", filename, re.IGNORECASE))
        year_html = (
            f'{year_label}<span class="pill">extra</span>' if is_extra else year_label
        )
        txt_path = derive_txt_path(p.get("local_path") or "")
        if txt_path:
            txt_e = html.escape(txt_path)
            cell = (
                f'<a class="doc-url" href="{txt_e}" target="_blank" '
                f'rel="noopener">{txt_e}</a>'
            )
        else:
            cell = '<span class="doc-missing">saknas</span>'
        items.append(
            f"<li>"
            f'<span class="doc-year">{year_html}</span>'
            f"{cell}"
            f"</li>"
        )
    return '<ul class="doc-list">' + "".join(items) + "</ul>"


PAGE_TEMPLATE = """<!DOCTYPE html>
<!--
    AUTO-GENERATED — do not edit by hand.
    Regenerate with: python3 generate_text_documents.py
    Source data:     sources.yaml
    Generator:       generate_text_documents.py
-->
<html lang="sv">
<head>
    <meta charset="UTF-8">
    <meta name="generator" content="generate_text_documents.py">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Textdokument - BRF Sjotungan</title>
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

        .doc-list {{
            list-style: none;
            padding: 0;
            margin: 0;
            border: 1px solid var(--vm-border);
            border-radius: 10px;
            background: #f7f9fd;
            overflow: hidden;
        }}

        .doc-list li {{
            display: flex;
            align-items: baseline;
            gap: 16px;
            padding: 10px 16px;
            border-bottom: 1px solid #dde6f4;
            font-size: 14px;
        }}

        .doc-list li:last-child {{
            border-bottom: none;
        }}

        .doc-list li:hover {{
            background: #eef3fb;
        }}

        .doc-year {{
            flex: 0 0 4.5rem;
            font-weight: 600;
            color: #33486f;
            white-space: nowrap;
        }}

        .rules-list .doc-year {{
            flex-basis: 10rem;
        }}

        .doc-text {{
            display: flex;
            flex-direction: column;
            gap: 4px;
            min-width: 0;
        }}

        .doc-note {{
            color: var(--vm-muted);
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

        .doc-missing {{
            color: #b0b8c7;
            font-style: italic;
        }}

        .pill {{
            display: inline-block;
            padding: 1px 8px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            background: var(--vm-card);
            color: var(--vm-blue);
            margin-left: 6px;
        }}

        .empty-state {{
            border: 1px dashed var(--vm-border);
            background: #f7f9fd;
            border-radius: 10px;
            color: var(--vm-muted);
        }}
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
            <h1 class="text-blue-900 font-extrabold text-3xl mb-2 border-b-4 border-blue-300 pb-2">Textdokument</h1>
            <p class="mb-6" style="color: var(--vm-muted);">
                Sökbara textversioner av föreningens årsredovisningar och stämmoprotokoll.
            </p>

            <section class="mb-10" aria-labelledby="legislation-heading">
                <h2 id="legislation-heading" class="text-xl font-bold text-blue-900 mb-3">Lagstiftning</h2>
                <p class="text-sm mb-4" style="color: var(--vm-muted);">
                    Nationell lagstiftning som styr bostadsrättsföreningar utöver de egna stadgarna. Bostadsrättsföreningar är ekonomiska föreningar och regleras av lag (2018:672) om ekonomiska föreningar tillsammans med bostadsrättslagen (1991:614).
                </p>
                <ul class="doc-list rules-list">
                    <li>
                        <span class="doc-year">SFS 2018:672</span>
                        <span class="doc-text">
                            <a class="doc-url" href="SFS2018672_fulltext_konsoliderad.pdf" target="_blank" rel="noopener">SFS2018672_fulltext_konsoliderad.pdf</a>
                            <span class="doc-note">Lag (2018:672) om ekonomiska föreningar – konsoliderad fulltext.</span>
                        </span>
                    </li>
                </ul>
            </section>

            <section class="mb-10" aria-labelledby="stadgar-heading">
                <h2 id="stadgar-heading" class="text-xl font-bold text-blue-900 mb-3">Stadgar</h2>
                <p class="text-sm mb-4" style="color: var(--vm-muted);">
                    Föreningens stadgar.
                </p>
                <ul class="doc-list rules-list">
                    <li>
                        <span class="doc-year">Stadgar 2023</span>
                        <a class="doc-url" href="https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/NYAStadgar2023.pdf" target="_blank" rel="noopener">https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/NYAStadgar2023.pdf</a>
                    </li>
                </ul>
            </section>

            <section class="mb-10" aria-labelledby="annual-reports-heading">
                <h2 id="annual-reports-heading" class="text-xl font-bold text-blue-900 mb-3">Årsredovisningar</h2>
                {annual_reports_html}
            </section>

            <section class="mb-10" aria-labelledby="stamma-protocols-heading">
                <h2 id="stamma-protocols-heading" class="text-xl font-bold text-blue-900 mb-3">Stämmoprotokoll</h2>
                {stamma_protocols_html}
            </section>

            <section class="mb-10" aria-labelledby="rules-heading">
                <h2 id="rules-heading" class="text-xl font-bold text-blue-900 mb-3">Regler</h2>
                <p class="text-sm mb-4" style="color: var(--vm-muted);">
                    Föreningens ordnings- och störningsregler.
                </p>
                <ul class="doc-list rules-list">
                    <li>
                        <span class="doc-year">Ordningsregler</span>
                        <a class="doc-url" href="https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/ordningsregler.pdf" target="_blank" rel="noopener">https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/ordningsregler.pdf</a>
                    </li>
                    <li>
                        <span class="doc-year">Störningsregler</span>
                        <a class="doc-url" href="https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/storningsregler.pdf" target="_blank" rel="noopener">https://www.sjotungan.se/public_html/new2016/images/pdf/stadgar/storningsregler.pdf</a>
                    </li>
                </ul>
            </section>

            <section aria-labelledby="links-heading">
                <h2 id="links-heading" class="text-xl font-bold text-blue-900 mb-3">Länkar</h2>
                <ul class="doc-list rules-list">
                    <li>
                        <span class="doc-year">Föreningens webbplats</span>
                        <a class="doc-url" href="https://www.sjotungan.se/" target="_blank" rel="noopener">https://www.sjotungan.se/</a>
                    </li>
                    <li>
                        <span class="doc-year">HSB-koden</span>
                        <span class="doc-text">
                            <a class="doc-url" href="https://www.hsb.se/contentassets/59739daf3b394c8e82ce49b90b79e664/hsb_kod_brf_dec2021_v2_211215.pdf" target="_blank" rel="noopener">https://www.hsb.se/contentassets/59739daf3b394c8e82ce49b90b79e664/hsb_kod_brf_dec2021_v2_211215.pdf</a>
                            <span class="doc-note">HSB:s kod för bostadsrättsförening – rekommendationer för styrning och kontroll. Sannolikt inte antagen av Sjötungan.</span>
                        </span>
                    </li>
                </ul>
            </section>
        </section>
    </main>
</body>
</html>
"""


def main() -> int:
    if not SOURCES_YAML.exists():
        print(f"sources.yaml not found at {SOURCES_YAML}", file=sys.stderr)
        return 1
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8")) or {}
    annual = render_annual_reports(data.get("annual_reports") or [])
    stamma = render_stamma_protocols(data.get("stamma_protocols") or [])
    page = PAGE_TEMPLATE.format(
        annual_reports_html=annual,
        stamma_protocols_html=stamma,
    )
    OUTPUT_PATH.write_text(page, encoding="utf-8")
    print(f"✅ Wrote {OUTPUT_PATH} ({len(page):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
