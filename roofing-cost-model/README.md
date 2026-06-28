# Roofing Cost Model — Home Depot Material Scraper

Material-only retail price model by geography. This module collects **Home
Depot** roofing product data by ZIP code, normalizes prices to dollars per
roofing **square** (100 sq ft), classifies products into a controlled material
taxonomy, and aggregates to ZIP / state / CBSA / national levels for downstream
analysis.

This is the first build: **asphalt shingles only**, retail and public bulk
pricing. The code is structured so metal panels and clay/concrete tile can be
added later by filling in URLs in the category config — no code changes needed
for classification (rules already cover those classes).

## Explain it to me like I'm 5

**What is this?** We want to know how much roofing material (like shingles)
costs in different parts of the country. This tool looks at Home Depot's website,
grabs the prices, and turns them into tidy spreadsheets we can study.

**Why do I have to help?** Home Depot has a "robot bouncer" that blocks programs
from reading its pages automatically. But it's totally fine for a *person* to
open a page in a normal browser. So the tool drives a real browser, and **you**
act as the human: you open the page and tell the tool "okay, grab this one."

**What will happen when I run it?**

1. You run one command (below). A **Chrome window pops open** on a Home Depot
   shingles page.
2. In that window, you set your store / ZIP code if it asks (just like normal
   shopping), and wait until you can see the shingle products with prices.
3. You switch back to the **terminal**. It is waiting and showing a prompt like:

   ```
   [zip=90001 category=asphalt_shingles page=0]
     URL: https://www.homedepot.com/b/...Roof-Shingles/...
     Set store/ZIP if needed, then press Enter to capture (s=skip, q=quit):
   ```

   - Press **Enter** → it saves that page. ✅
   - Type **s** then Enter → skip this page.
   - Type **q** then Enter → quit.

   If it warns that the page "looks like a challenge page," that means the robot
   bouncer showed up — fix it in the browser (solve the puzzle / refresh) and
   press Enter again.
4. When you're done capturing, you run **two more commands** that clean up the
   prices and build the final spreadsheets. No browser needed for those.

**The whole thing, copy-paste:**

```powershell
# 0) one-time setup
cd roofing-cost-model
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install playwright
playwright install chromium

# 1) CAPTURE — a browser opens; set your ZIP, then press Enter in the terminal
python -m src.retailers.home_depot --save-page --zip-codes 90001 --categories asphalt_shingles

# 2) READ the saved page(s) into raw product rows
python -m src.retailers.home_depot --local-html-dir local_pages --categories asphalt_shingles

# 3) CLEAN UP prices into per-square numbers
python -m src.normalize_products --input data_raw/home_depot/<DATE>/home_depot_products_raw.jsonl --output data_intermediate/home_depot_products_normalized.parquet

# 4) BUILD the summary spreadsheets (by ZIP / state / metro / national)
python -m src.aggregate_prices --input data_intermediate/home_depot_products_normalized.parquet --out-dir data_output
```

Replace `<DATE>` with today's folder name (e.g. `2026-06-27`) that step 2
created under `data_raw/home_depot/`. Your final results land in
`data_output/` as CSV files. That's it!

> Want more ZIPs or pages? Add them: `--zip-codes 90001,33101` and
> `--max-pages 2`. The tool will walk you through each page the same way.

## Project layout

```
roofing-cost-model/
  config/
    home_depot_categories.yml   # category -> listing URL + expected group
    geo_seed_zips.csv           # ZIP -> city/state/CBSA seed
  data_raw/home_depot/          # raw scrape output (HTML + JSONL), per day
  data_intermediate/            # normalized products (parquet + csv)
  data_output/                  # aggregated price tables (csv)
  src/
    retailers/
      home_depot.py             # scraper (requests first, Playwright fallback)
    normalize_products.py       # price/coverage parsing + per-square metrics
    classify_materials.py       # rule-based material classification
    aggregate_prices.py         # ZIP/state/CBSA/national aggregation
  tests/
    test_classify_materials.py
    test_normalize_products.py
```

## Setup

```powershell
cd roofing-cost-model
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# Optional browser fallback (only if the requests path returns no products):
# pip install playwright
# playwright install chromium
```

## Pipeline

The pipeline is three stages: **scrape → normalize → aggregate**.

### 1. Scrape

```powershell
python -m src.retailers.home_depot --zip-codes 90001,33101 --categories asphalt_shingles --max-pages 2
```

Key options:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--zip-codes` | seed file | Comma-separated ZIP override |
| `--categories` | all in config | Comma-separated category-key override |
| `--out-dir` | `data_raw/home_depot` | Raw output root |
| `--max-pages` | unlimited | Page cap per ZIP/category (use for testing) |
| `--sleep-seconds` | `2` | Base delay between page requests |
| `--headless` | off | Run the browser fallback headless |
| `--no-playwright` | off | Disable the browser fallback entirely |
| `--local-html-dir` | none | Parse pre-saved HTML files instead of hitting the network |
| `--save-page` | off | Guided browser capture of category pages (see below) |
| `--save-dir` | `local_pages` | Destination directory for `--save-page` captures |

Raw output for each run day:

```
data_raw/home_depot/YYYY-MM-DD/
  raw/<category>_<zip>_p<page>.html     # raw HTML for audit
  home_depot_products_raw.jsonl         # extracted product rows
  home_depot_errors.jsonl               # per-request failures
```

Every row records `scrape_timestamp`, `source_url`, and `raw_html_path` for
auditability. The scraper respects `robots.txt`, rate-limits requests, and
retries transient failures with exponential backoff. It uses only public pages
and never logged-in Pro pricing.

#### Home Depot anti-bot protection (important)

Home Depot fronts its site with **Akamai Bot Manager**. In practice:

- The `requests` path returns **HTTP 403** on category pages.
- A headless/headful Playwright navigation (even with realistic UA, ZIP
  cookies, `navigator.webdriver` removed, and a homepage warm-up) is served an
  Akamai **challenge/error page** ("Oops!! Something went wrong"), which the
  scraper detects via `_looks_blocked()` and logs as `no_products_extracted`.

Reliably bypassing this would require residential proxies and sensor/CAPTCHA
solving — an arms race that this project intentionally does **not** pursue
(per the "do not scrape aggressively / public endpoints only" constraints).
Instead, use one of these sanctioned routes:

1. **Local-HTML ingestion (recommended, zero-cost).** Open the category page in
   your normal browser, save the page as HTML, drop it in a folder named with
   the convention `<category>_<zip>_pN.html`, and run:

   ```powershell
   python -m src.retailers.home_depot --local-html-dir local_pages --categories asphalt_shingles
   ```

   The saved pages flow through the **identical** extract → normalize →
   aggregate pipeline (`source_method = "local_html"`).

   To standardize that capture step, use the built-in guided helper instead of
   saving by hand:

   ```powershell
   python -m src.retailers.home_depot --save-page --zip-codes 90001 --categories asphalt_shingles
   ```

   This opens a real browser (persistent profile under
   `local_pages/.browser_profile`, so an Akamai challenge stays solved between
   captures). For each category page it pauses at a terminal prompt — set your
   store/ZIP in the browser, then press **Enter** to capture (`s` to skip, `q`
   to quit). Files are written as `<category>_<zip>_pN.html` ready for
   `--local-html-dir`. Use `--max-pages N` to walk pagination and `--save-dir`
   to change the destination. If a capture still looks like a challenge page,
   the helper warns so you can re-capture.

2. **A managed unblocker / official feed.** Point the fetch step at a licensed
   service (e.g. an enterprise web-unlocker API) or Home Depot's official
   product API/affiliate feed. The parser only needs the page HTML, so any
   sanctioned source can supply it.

### 2. Normalize

```powershell
python -m src.normalize_products --input data_raw/home_depot/YYYY-MM-DD/home_depot_products_raw.jsonl --output data_intermediate/home_depot_products_normalized.parquet
```

Produces a Parquet **and** a sibling CSV with one row per product, including
`price_per_square`, `bulk_price_per_square`, `bulk_discount_pct`,
`material_class`, joined geography, and a `coverage_flag`
(`ok` / `missing` / `suspicious`). Missing coverage is **not** imputed.

### 3. Aggregate

```powershell
python -m src.aggregate_prices --input data_intermediate/home_depot_products_normalized.parquet --out-dir data_output
```

Writes four CSVs (ZIP, state, CBSA, national), each grouped by
`scrape_date × retailer × geography × material_class` with median / p25 / p75 /
min / max price per square, product and store counts, and median bulk metrics.

## Material taxonomy

`material_class` is one of:

`asphalt_3_tab`, `asphalt_architectural`, `asphalt_premium_architectural`,
`metal_corrugated_panel`, `metal_standing_seam`, `tile_clay`, `tile_concrete`,
`tile_unspecified`, `unclassified`.

Classification is deterministic and rule-ordered (first match wins) — see
`src/classify_materials.py`.

## Tests

```powershell
cd roofing-cost-model
pytest -q
```

## Notes & limitations

- **Live Home Depot scraping is blocked by Akamai Bot Manager** (verified: 403
  on the `requests` path; challenge page on Playwright). Use `--local-html-dir`
  or a sanctioned unblocker/official feed (see the scrape section above). The
  parser, normalizer, classifier, and aggregator are all source-agnostic.
- Home Depot localizes pricing via cookies/internal endpoints that are not a
  stable public contract; ZIP context is set best-effort. Always treat
  `coverage_flag` and store/ZIP provenance as part of QA.
- The downstream schema is identical regardless of `source_method`
  (`requests` / `playwright` / `local_html`).
- This model captures **field covering material only** — not underlayment,
  flashing, fasteners, waste, delivery, or markup.

## BLS/FRED Index Module (Time Escalation)

This repository also includes a **national index escalation module** used to
time-adjust material prices from retailer scrapes.

Important scope:

- These are **national monthly inflation indexes**, not local market prices.
- They are used only for historical escalation/backcasting across months.
- Local geography and level differences still come from retailer scrape data.

### Files

- `config/bls_fred_series.yml`
- `src/fetch_fred_indexes.py`
- `src/build_material_escalation_factors.py`
- `notebooks/01_bls_fred_index_analysis.ipynb`
- `tests/test_escalation_factors.py`

### Series config

`config/bls_fred_series.yml` contains candidate FRED/BLS series for:

- prepared asphalt and tar roofing products
- asphalt input proxy
- sheet metal / steel / aluminum proxies
- clay and concrete product proxies
- construction materials aggregate proxy
- optional freight proxy

Each entry has:

- `series_id`
- `series_name`
- `source`
- `material_mapping`
- `use_case`
- `priority`
- `notes`

All IDs are intentionally marked for human verification before production use.

### Run the index fetch

```powershell
cd roofing-cost-model

# Preferred: use FRED API key
$env:FRED_API_KEY = "your_fred_api_key"
python -m src.fetch_fred_indexes

# Optional: force public CSV fallback (no key)
python -m src.fetch_fred_indexes --no-api
```

Outputs:

- Raw per-series snapshots: `data_raw/fred/*.csv`
- Normalized table: `data_intermediate/fred_indexes.parquet`

Normalized columns:

- `series_id`
- `series_name`
- `date`
- `value`
- `source`
- `material_mapping`
- `use_case`
- `priority`
- `pulled_at`

### Build escalation factors

```powershell
python -m src.build_material_escalation_factors --base-month 2024-01
```

Output:

- `data_output/material_escalation_factors.parquet`

Columns:

- `material_class`
- `series_id`
- `series_name`
- `date`
- `month`
- `index_value`
- `base_month`
- `base_index_value`
- `escalation_factor`
- `priority`
- `source`

Where:

- `escalation_factor = index_value / base_index_value`
- In the base month, factor is 1.0

### Notebook analysis

Use `notebooks/01_bls_fred_index_analysis.ipynb` to:

- plot each candidate index over time
- plot escalation factors by material class
- compare asphalt-related vs metal-related indexes
- identify missing months
- report latest observation date by series

### Tests

```powershell
python -m pytest tests/test_escalation_factors.py -q
```

### How it connects to scrape pricing

The retailer pipeline produces local observed prices by material class and date.
The index module provides national time multipliers by material class and month.

Use both together:

- keep local level from retailer scrape outputs
- apply index factor ratio to translate values between months

Example:

- `adjusted_price = observed_price(t0) * index(t1) / index(t0)`

This keeps geography sourced from retailer data while adding consistent temporal
normalization across the full historical series.
```
