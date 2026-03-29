# sjotungan/analytics

## Preparation

## Sweden Macro Inflation Data (KPI)

Inflation data used by the visualizations is stored locally in `data/macro_sweden.json`.

- Current local snapshot in this repo: **1980-2024** (`inflation_yoy_pct`).
- Source: **SCB KPI (year-over-year %)**, manually compiled into the JSON file.
- Why local: reproducible charts without runtime dependency on external APIs.

### Historical coverage policy

- The 2012 start year is a project choice, not a source limitation.
- We can extend the series to at least **1980** using the same SCB KPI source.
- If extended, keep the same schema and add yearly rows in ascending order.

### Update checklist

When updating `data/macro_sweden.json`:

1. Add or revise yearly `inflation_yoy_pct` values.
2. Keep `sources.inflation_yoy_pct` filled with name + URL.
3. Update `updated_at` to the change date.
4. Mention the year range change in the commit message.

### Provisioning sources file

Add PDF sources to `sources.yaml` file

### Downloading pdf files locally

```bash
python3 download_sources.py
```

### Extract data from PDF files to raw CSV files

Task LLM to extract data from pdf files in raw csv format:

```csv
category,amount
```

For example,

```csv
category,amount
Nettoomsättning,49194715
Övriga rörelseintäkter,183452
Summa Rörelseintäkter,49378167
Driftkostnader,-34361150
Övriga externa kostnader,-1094944
```

`data/annual_reports/<file_name>.pdf` data should be extracted to `data/annual_reports/<file_name>.csv` file.

#### Extraction notes

- **Newer reports** (2019+) have selectable text — PyPDF2 can extract text directly.
- **Older reports** (2013–2016) are scanned images — require OCR via `pytesseract` + `pdf2image`.
- Use `# SECTION NAME (sida N)` comments to separate sections (Resultaträkning, Balansräkning, Kassaflödesanalys, Noter).
- Amounts are integers in SEK, negative for costs/expenses.
- Cross-validate extracted numbers against the **prior-year comparison column** in adjacent reports (each report includes two years).
- The 2013 report uses **K2 accounting** (different income statement structure — costs grouped as "Drift", "Planerat underhåll", "Fastighetsskatt" rather than "Driftkostnader", "Övriga externa kostnader", "Personalkostnader"). From 2014 onwards **K3 accounting** is used.
- Some early CSVs (`kallelse_stamma2018.csv`) use a multi-column format (`section,item,note,<year>,<year>`). The preferred format is the simpler `category,amount`.

### Extract data from PDF files in structured form

#### Extract to `data/motions.csv`

Board recommendation on member motions ("Styrelsens yttrande" / "Styrelsens slutsats").

**Format:**

```csv
year,file,motion_number,page,title,authors,resolution,resolution_page,resolution_x,resolution_y,resolution_width,resolution_height
```

- `year` — motion/meeting year (e.g. 2025 from `stamma2025.pdf`)
- `file` — PDF filename in `data/annual_reports/`
- `motion_number` — motion identifier within that meeting document (e.g. `1`, `2`)
- `page` — page where the motion starts (or closest detected motion header)
- `title` — motion title (`Motion N` when only motion number is available)
- `authors` — motion proposers (optional; empty if not reliably extractable)
- `resolution` — normalized board stance/outcome: `Tillstyrker`, `Avstyrker`, `Delvis tillstyrker`, `Bifalls`, `Avslås`, `Besvarad`, `Oklar`
- `resolution_page` — page containing the board's explicit recommendation sentence
- `resolution_x,resolution_y,resolution_width,resolution_height` — optional highlight box coordinates (blank by default)

Use the reusable script:

```bash
python3 extraction/scripts/extract_motion_resolutions.py 2025 stamma2025.pdf
```

Append new rows to `data/motions.csv`:

```bash
python3 extraction/scripts/extract_motion_resolutions.py 2025 stamma2025.pdf --append
```

Backward-compatible shortcut still exists at project root:

```bash
python3 extract_motion_resolutions.py 2025 stamma2025.pdf --append
```

**Method overview:**

1. Extract each page with `pdftotext`.
2. Detect recommendation phrases (e.g. `yrkar styrelsen ... avslag/avslås` or `... bifall`).
3. Backtrack to the nearest `MOTION N` heading to attach motion context.
4. Normalize to a categorical `resolution` value for cross-year comparison.

**Notes for other years:**

- Text-selectable PDFs (newer years) should work directly.
- Scanned PDFs may require OCR fallback (future extension if needed).
- Start with dry-run output before appending, then manually verify page references.

#### Extract to `data/financial_states.csv`

Balance sheet snapshot values at year-end, with PDF source coordinates for highlighting.

**Format:**

```csv
year,category_id,amount,file,page,x,y,width,height
```

- `year` — fiscal year-end (e.g. 2024 = state at 2024-12-31)
- `category_id` — references `data/state_categories.csv`
- `amount` — integer in SEK
- `file` — PDF filename in `data/annual_reports/`
- `page,x,y,width,height` — highlight coordinates in rendered canvas space (PDF points × scale factor)

**Categories** (`data/state_categories.csv`):

| id | name | description |
|----|------|-------------|
| 0 | Övriga skulder till kreditinstitut | Short-term portion of bank loans (next year's amortization) |
| 1 | Skulder till kreditinstitut | Long-term bank loans |
| 2 | Kassa och bank | Cash and bank accounts |
| 3 | Avräkningskonto HSB Stockholm | HSB clearing account |
| 4 | Placeringskonto HSB Stockholm | HSB investment account |
| 5 | Kortfristiga placeringar | Short-term investments / money market funds |

**Source mapping:**

| Year(s) | Source PDF | Notes |
|---------|-----------|-------|
| 2024 | `stamma2025.pdf` | Text-selectable, pdfplumber for coords |
| 2023 | `stamma-2024.pdf` | Text-selectable |
| 2022 | `stamma-2023.pdf` | Text-selectable |
| 2021, 2020 | `stamma_kallelse_2022.pdf` | Text-selectable, has comparison column |
| 2019, 2018 | `BRF_Sjotungan_arsredovisning_2019.pdf` | Text-selectable, has comparison column |
| 2017 | `kallelse_stamma_2019.pdf` | Scanned, OCR coords |
| 2016 | `kallelse_stamma2018.pdf` | Scanned, OCR coords |
| 2015 | `arsredovisning2015.pdf` | Scanned, OCR coords |
| 2014 | `arsredovisning2014.pdf` | Scanned, OCR coords |
| 2013, 2012 | `arsredovisning_2013.pdf` | Scanned, OCR coords. 2012 from comparison column |

**Extraction notes:**

- Each annual report contains the current year and previous year (comparison column). Earlier years can be sourced from the comparison column of the next report.
- Debt split (cat 0 vs 1): from **Note 16** ("Skulder till kreditinstitut") which shows "Långfristiga exkl kortfristig del" and "Nästa års amortering".
- **2012**: the 2013 report only shows total debt (137,493,781) without the short/long split for the comparison year. Cat 0 is set to 0 and cat 1 holds the full total.
- Categories 3–5 (HSB accounts, short-term investments) are only available in years where those items appear on the balance sheet. Newer reports (2023+) merged these into cat 2.
- **Scanned PDFs** (2013–2017): coordinates determined via OCR (`pytesseract` + `pdf2image`), then multiplied by the PDF rendering scale factor (1.49× for `arsredovisning_2013.pdf`, varies by PDF).
- **Text-selectable PDFs** (2018+): coordinates found via `pdfplumber` word extraction.

#### Extract to `data/financial_events.csv`

Cash flow events (income, expenses, investments, financing) by year with PDF source coordinates.

**Format:**

```csv
year,category_id,amount,file,page,x,y,width,height
```

- `category_id` — references `data/event_categories.csv`
- `amount` — integer in SEK (negative for outflows)
- Categories with `parent` in `event_categories.csv` are sub-items (e.g. cat 2 "Fastighetsskötsel" is a child of cat 1 "Driftkostnader")
- **Invariant:** the sum of all top-level events (those without a `parent`) for a given year must equal the change in total liquidity (state categories 2+3+4+5) between year-end and prior year-end.

**Categories** (`data/event_categories.csv`):

| id | name | Source | parent |
|----|------|--------|--------|
| 0 | Nettoomsättning | Income statement | — |
| 1 | Driftkostnader | Income statement | — |
| 2 | Fastighetsskötsel och lokalvård | Note (Driftkostnader) | 1 |
| 3 | Kassaflöde från finansieringsverksamheten | Cash flow statement | — |
| 4 | Övriga intäkter | Income statement | — |
| 5 | Övriga externa kostnader | Income statement | — |
| 6 | Personalkostnader | Income statement | — |
| 7 | Räntekostnader | Income statement | — |
| 8 | Investeringar | Cash flow statement | — |
| 9 | Rörelsekapital - ej insamlade intäkter | Cash flow statement | — |
| 10 | Rörelsekapital - ej betalda kostnader | Cash flow statement | — |
| 11 | Leverantörsskulder | Cash flow statement | — |
| 12 | Övriga kortfristiga skulder | Cash flow statement | — |
| 13 | Kundfordringar | Cash flow statement | — |
| 14 | Övriga fordringar | Cash flow statement | — |
| 15 | Erhållen ränta | Income statement | — |
| 16 | Planerat underhåll | Income statement | — |
| 17 | Årsavgifter | Note (Nettoomsättning) | 0 |
| 18 | Hyror | Note (Nettoomsättning) | 0 |
| 19 | Övriga intäkter (nettoomsättning) | Note (Nettoomsättning) | 0 |
| 20 | Avgifts- och hyresbortfall | Note (Nettoomsättning) | 0 |
| 21 | Reparationer | Note (Driftkostnader) | 1 |
| 22 | El | Note (Driftkostnader) | 1 |
| 23 | Uppvärmning | Note (Driftkostnader) | 1 |
| 24 | Vatten | Note (Driftkostnader) | 1 |
| 25 | Sophämtning | Note (Driftkostnader) | 1 |
| 26 | Fastighetsförsäkring | Note (Driftkostnader) | 1 |
| 27 | Kabel-TV och bredband | Note (Driftkostnader) | 1 |
| 28 | Fastighetsskatt och fastighetsavgift | Note (Driftkostnader) | 1 |
| 29 | Förvaltningsarvoden | Note (Driftkostnader) | 1 |
| 30 | Övriga driftkostnader | Note (Driftkostnader) | 1 |
| 31 | Underhåll | Note (Driftkostnader) | 1 |
| 32 | Försäkringsskador | Note (Driftkostnader) | 1 |
| 33 | Bevakningskostnader | Note (Övriga externa kostnader) | 5 |
| 34 | Hyror och arrenden | Note (Övriga externa kostnader) | 5 |
| 35 | Förbrukningsinventarier och varuinköp | Note (Övriga externa kostnader) | 5 |
| 36 | Administrationskostnader | Note (Övriga externa kostnader) | 5 |
| 37 | Extern revision | Note (Övriga externa kostnader) | 5 |
| 38 | Konsultkostnader | Note (Övriga externa kostnader) | 5 |
| 39 | Medlemsavgifter | Note (Övriga externa kostnader) | 5 |
| 40 | Juristarvode | Note (Övriga externa kostnader) | 5 |
| 41 | Föreningsverksamhet | Note (Övriga externa kostnader) | 5 |
| 42 | Övriga förvaltningskostnader | Note (Övriga externa kostnader) | 5 |
| 43 | Underhållsplan | Note (Övriga externa kostnader) | 5 |
| 44 | Styrelsearvoden | Note (Personalkostnader) | 6 |
| 45 | Revisionsarvode | Note (Personalkostnader) | 6 |
| 46 | Övriga arvoden | Note (Personalkostnader) | 6 |
| 47 | Sociala avgifter | Note (Personalkostnader) | 6 |
| 48 | Övriga personalkostnader | Note (Personalkostnader) | 6 |
| 49 | Löner och övriga ersättningar | Note (Personalkostnader) | 6 |
| 50 | Fastighetsskötsel | Note (Driftkostnader) | 2 |
| 51 | Städning | Note (Driftkostnader) | 2 |
| 52 | Hisstillsyn | Note (Driftkostnader) | 2 |
| 53 | Tillsyn och besiktning | Note (Driftkostnader) | 2 |
| 54 | Trädgårdsskötsel | Note (Driftkostnader) | 2 |
| 55 | Snöröjning | Note (Driftkostnader) | 2 |

**How to extract events for a new year:**

1. **Prerequisite:** financial states for the target year AND the prior year must already exist in `financial_states.csv`. Compute the expected cash change: `liquidity(year) - liquidity(year-1)` where liquidity = sum of state categories 2, 3, 4, 5.

2. **Identify source PDF.** Check `sources.yaml` for which PDF covers the target `finance_period`. The annual report for year N is filed in the report published the following year (e.g., 2016 events come from `arsredovisning_2016.pdf`).

3. **Extract values from two sections:**

   - **Income statement** (Resultaträkning) — typically page 6–7 in older reports, page 27 in newer ones:
     - `Nettoomsättning` → cat 0
     - `Drift och underhåll` → cat 1 (negative)
     - `Planerat underhåll` → cat 16 (negative, may be zero)
     - `Övriga externa kostnader` → cat 5 (negative)
     - `Personalkostnader och arvoden` → cat 6 (negative)
     - `Ränteintäkter` → cat 15
     - `Räntekostnader` → cat 7 (negative)
     - `Övriga intäkter` → cat 4 (if present as separate line; some years fold it into cat 0)

   - **Cash flow statement** (Kassaflödesanalys) — typically page 9 in older reports, page 13 or 30 in newer ones:
     - Working capital changes (sign as shown in the statement):
       - `Ökning/minskning kortfristiga fordringar` → cat 9 (earlier years) or split into cat 13 + cat 14 (later years)
       - `Ökning/minskning kortfristiga skulder` → cat 10 (earlier years) or split into cat 11 + cat 12 (later years)
     - `Investeringar` (total of investment section) → cat 8 (negative)
     - `Kassaflöde från finansieringsverksamheten` → cat 3

   **K2 accounting (2013):** The income statement has a different cost structure:
   - "Drift" (Note 2) includes Personalkostnader — must be separated.
   - "Fastighetsskatt" is a separate IS line — must be folded into cat 1 (Driftkostnader).
   - No separate "Övriga externa kostnader" line (cat 5 omitted).
   - K2→K3 mapping: `cat 1 = -(Drift − Personalkostnader + Fastighetsskatt)`. The OCR search key is the Drift digit string, but the stored amount is the adjusted K3-compatible value.
   - `Personalkostnader` (cat 6) is extracted from Note 2 breakdown instead of the IS.
   - Investments (cat 8) may be positive when projects are completed (capitalized) or assets sold.

4. **Get PDF coordinates** for each value. Coordinates in the CSV are in **canvas space** — the HTML viewer draws highlights directly at these values without further scaling.

   - **Text-selectable PDFs** (2018+): use `pdfplumber` to extract words with bounding boxes. Coordinates are in PDF points; **multiply x, y, width, height by `1.5`** for canvas.
   - **Scanned PDFs** (2013–2017): use `pytesseract.image_to_data()` to get OCR bounding boxes in pixels, then convert to PDF points (multiply by `pdf_width / image_width`), then **multiply by `1.5`** for canvas.

   **⚠️ Common pitfall — coordinate scale:** All coordinates in the CSV must be in **canvas pixel space** (PDF points × 1.5). The HTML viewer draws highlights directly at these values with `ctx.fillRect(x, y, w, h)` — no further scaling is applied at render time. If you store raw pdfplumber or raw OCR coordinates without the ×1.5 factor, highlights will appear in the wrong position.

   **⚠️ Common pitfall — scanned pages returning no text:** Some PDF pages are scanned images with no extractable text layer. `pdfplumber.extract_words()` returns an empty list for these pages. If coordinate extraction returns `(0, 0)`, the page is likely scanned. Use OCR instead: try DPI=300 first, then DPI=400 if amounts aren't found (lower-quality scans need higher resolution). See `fix_zero_coordinates.py` for the OCR fallback approach.

   Standard highlight box: `width=100, height=20` (canvas pixels).

   **OCR pitfalls for scanned reports:**
   - The income statement has two year columns (current + comparison). OCR often merges digits from both columns into one block. Filter tokens by x-position: `left >= 1600px` to exclude labels/notes, then split at `~1950px` to separate the current-year column from the comparison column.
   - OCR may split a single number (e.g., "20 452 772") into multiple tokens. Group tokens into rows by y-position (±15px tolerance), then concatenate all digits per row to match target amounts.
   - OCR may insert artifact characters (e.g., colons: "36083:819"). The `_strip()` function removes `:`, `;`, commas, dots, dashes, and spaces before matching.
   - **PSM mode:** Some scanned PDFs (e.g., 2013 K2 format) require tesseract page segmentation mode 6 (`--psm 6`) instead of the default 3. Set `'psm': 6` in the year's config when the default mode misses rows.
   - Use `extract_scanned_events.py` as the extraction tool — it handles these pitfalls via `find_amount_coords()`. To extract a new year, add a config entry to `CONFIGS` with the PDF filename, page numbers, amounts dict, column-split threshold, and optionally `psm`. Then run:

     ```bash
     python3 extract_scanned_events.py <YEAR>            # dry-run (prints CSV lines)
     python3 extract_scanned_events.py <YEAR> --append    # appends to financial_events.csv
     ```

   Typical coordinate format: `page,x,y,width,height` where (x,y) is top-left corner.

5. **Add rows** to `data/financial_events.csv`. Use the year's own annual report as `file` (e.g., `arsredovisning_2016.pdf` for 2016 events).

6. **Verify.** Sum all top-level events (categories without a `parent`) for the year. This must equal the expected cash change from step 1. Use `verify_<year>_events.py` or:

   ```bash
   python3 -c "
   import csv
   YEAR = '2016'
   with open('data/financial_events.csv') as f:
       events = list(csv.DictReader(f))
   with open('data/event_categories.csv') as f:
       cats = {int(r['id']): r for r in csv.DictReader(f)}
   with open('data/financial_states.csv') as f:
       states = list(csv.DictReader(f))
   liq = lambda y: sum(int(s['amount']) for s in states if s['year']==str(y) and int(s['category_id']) in (2,3,4,5))
   expected = liq(int(YEAR)) - liq(int(YEAR)-1)
   actual = sum(int(e['amount']) for e in events if e['year']==YEAR and not cats[int(e['category_id'])]['parent'])
   print(f'Expected: {expected:>12,}')
   print(f'Actual:   {actual:>12,}')
   print(f'Diff:     {actual-expected:>12,}')
   "
   ```

   A difference of 0–1 SEK (rounding) is acceptable.

**Extracting note subcategories:**

Three income statement categories have note-level breakdowns extracted via dedicated scripts:

- **Driftkostnader** (cat 1 → subcategories 2, 21–32): `python3 extract_driftkostnader.py`
- **Övriga externa kostnader** (cat 5 → subcategories 33–43): `python3 extract_ovriga_externa.py`
- **Personalkostnader** (cat 6 → subcategories 44–49): `python3 extract_personalkostnader.py`

Each script:
1. Contains hardcoded amounts compiled from the annual report CSVs for all years.
2. Attempts to find PDF bounding-box coordinates via `pdfplumber` (text-selectable PDFs only; scanned PDFs get placeholder `0,0,100,20`).
3. The scripts apply the ×1.5 canvas scaling to `pdfplumber` coordinates internally — output is already in canvas space.
4. For scanned PDFs where `pdfplumber` returns no words, run `fix_zero_coordinates.py` to extract coordinates via OCR and replace the `0,0` placeholders. That script also outputs canvas-scaled coordinates.
5. Prints CSV rows to stdout — pipe to `financial_events.csv`:
   ```bash
   python3 extract_ovriga_externa.py 2>/dev/null | grep -E '^[0-9]{4},' >> data/financial_events.csv
   ```
6. Verifies that subcategory sums match the parent category totals (1 SEK rounding tolerance).

To add a new year, add the subcategory amounts to the `data` dict and the PDF source to `sources` in the relevant script, then re-run.

**Extraction progress:**

| Year | Status | Source PDF | Notes |
|------|--------|-----------|-------|
| 2024 | ✅ | `stamma2025.pdf` | Includes working capital sub-items (cats 11–14) |
| 2023 | ✅ | `stamma-2024.pdf` | |
| 2022 | ✅ | `stamma-2023.pdf` | |
| 2021 | ✅ | `stamma_kallelse_2022.pdf` | |
| 2020 | ✅ | `stamma_kallelse-2021.pdf` | |
| 2019 | ✅ | `BRF_Sjotungan_arsredovisning_2019.pdf` | |
| 2018 | ✅ | `kallelse_stamma_2019.pdf` | Scanned, OCR coords |
| 2017 | ✅ | `kallelse_stamma2018.pdf` | Scanned, OCR coords |
| 2016 | ✅ | `arsredovisning_2016.pdf` | Scanned, OCR coords |
| 2015 | ✅ | `arsredovisning2015.pdf` | Scanned, OCR coords |
| 2014 | ✅ | `arsredovisning2014.pdf` | Scanned, OCR coords. 20 SEK rounding diff (balance sheet state rounding). |
| 2013 | ✅ | `arsredovisning_2013.pdf` | K2 accounting, scanned, OCR coords (PSM 6, K2→K3 mapping). 1 SEK rounding diff. |
| 2012 | ❌ | `arsredovisning_2013.pdf` | Comparison column. Needs 2011 states first (no source PDF for 2012's own report). |

#### Extract to `data/general_states.csv`

General non-financial data per year: fiscal period, board chairman, vice chairman, and board members, with PDF source coordinates.

**Format:**

```csv
year,category_id,value,file,page,x,y,width,height
```

- `category_id` — references `data/general_categories.csv`
- `value` — text value (name or date range). Multi-person fields (cats 3, 4, 5) are semicolon-separated.
- `page` — 1-based page number (matches pdf.js `getPage()` convention)
- Coordinates are in **canvas pixel space** (PDF points × 1.5), same convention as `financial_states.csv`.

**Categories** (`data/general_categories.csv`):

| id | name | description |
|----|------|-------------|
| 0 | Räkenskapsår | Fiscal year period (e.g. "2024-01-01 – 2024-12-31") |
| 1 | Ordförande | Chairman |
| 2 | Vice ordförande | Vice chairman (empty if not listed separately) |
| 3 | Ledamöter | Board members (semicolon-separated names) |
| 4 | Valberedning | Nomination committee members (semicolon-separated names) |
| 5 | Revisorer | Auditors: elected revisor(s) + HSB-appointed firm (semicolon-separated) |

**Source pages:**

- Board composition is found in the förvaltningsberättelse (management report) section, typically labelled "Styrelsens sammansättning" or "Styrelsen har utgjorts av".
- For reports with two compositions (before/after stämma), the post-stämma (end-of-year) board is used.
- Valberedning and revisorer are listed in the same förvaltningsberättelse section, typically a few paragraphs after board composition.
- Revisorer (cat 5) include both the elected revisor(s) and the HSB-appointed auditing firm (e.g. "Mattias Matti;BoRevision" or "Mattias Matti;Kungsbron Borevision").
- **2019–2024** are text-selectable; coordinates extracted via `pdfplumber` × 1.5.
- **2013–2018** are scanned PDFs; coordinates extracted via OCR (`pytesseract`) × canvas scale.

## Previewing pages locally

Start a local HTTP server from the project root:

```bash
python3 -m http.server 8000
```

Then open in your browser:

| Page | URL |
|------|-----|
| Ekonomisk utveckling | [http://localhost:8000/changing_over_time.html](http://localhost:8000/changing_over_time.html) |
| Kassaflödesdetaljer | [http://localhost:8000/annual_details.html](http://localhost:8000/annual_details.html) |
| Styrelseledning | [http://localhost:8000/board_leadership.html](http://localhost:8000/board_leadership.html) |

The pages load CSV data and PDF sources via `fetch()`, so they must be served over HTTP (opening the `.html` files directly won't work due to CORS).

To stop the server: press `Ctrl+C`, or from another terminal:

```bash
lsof -ti:8000 | xargs kill
```
