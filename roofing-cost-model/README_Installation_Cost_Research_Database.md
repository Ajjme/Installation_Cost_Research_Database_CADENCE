# Roofing Cost Model — Home Depot Material Scraper

Material-only retail price model by geography. This module collects **Home
Depot** roofing product data by ZIP code, normalizes prices to dollars per
roofing **square** (100 sq ft), classifies products into a controlled material
taxonomy, and aggregates to ZIP / state / CBSA / national levels for downstream
analysis.

The initial collection targets **asphalt shingles**, retail and public bulk
pricing. The same pipeline can collect metal panels and clay/concrete roof tile
after their public category URLs are added to the category config. Classification
rules already cover those material types.

## Explain it to me like I'm 5

**What is this?** We want to know how much roofing material (like shingles)
costs in different parts of the country. This tool looks at Home Depot's website,
grabs the prices, and turns them into tidy spreadsheets we can study.

**Why do I have to help?** Home Depot frequently blocks automated browsers. The
reliable collection method is to open each public category page in your normal
Chrome browser, set the target ZIP/store, and save the fully loaded page. The
tool then reads that local HTML without contacting Home Depot.

**What will happen when I run it?**

1. Open regular Chrome from the Applications menu, not the automated Playwright
   browser.
2. Open the configured Home Depot category page and complete any challenge
   normally. Do not repeatedly refresh the page.
3. Set the target store or delivery ZIP and confirm the page displays that ZIP.
4. Wait for product names and prices to appear, then scroll through the page so
   lazy-loaded products are present.
5. Select **File > Save Page As**, choose **Webpage, Complete**, and use the
   filename `<category>_<zip>_p<page>.html`. For example:

   ```text
   asphalt_shingles_27701_p0.html
   ```

6. Save all asphalt, metal, and other roofing captures collected that day in one
   dated directory, such as `local_pages/2026-07-31/`. The HTML filenames retain
   the category and ZIP, so materials and locations remain attributable.
7. Run the batch command below. No browser is needed after the HTML has been
    saved.

### Batch command for all roofing materials

The batch runner detects category keys from the saved filenames and performs
ingestion, normalization, and aggregation in one command:

```bash
python -m src.process_home_depot_batch \
  --local-html-dir local_pages/2026-07-31
```

The local directory name becomes the batch name. Each batch therefore receives
separate raw, normalized, and aggregate paths:

```text
data_raw/home_depot/<batch-name>/
data_intermediate/home_depot_products_normalized_<batch-name>.parquet
data_intermediate/home_depot_products_normalized_<batch-name>.csv
data_output/home_depot/<batch-name>/
```

The command refuses to overwrite any existing batch artifact. Use one new dated
directory for each collection day. Saved filenames must still follow
`<category>_<zip>_p<page>.html`; both `asphalt_shingles` and `metal_shingles`
are registered category keys.

Metal panel area is calculated only when the listing includes both dimensions,
such as `26 in. x 8 ft.`. Products that show only panel length remain in the
normalized files with their unit price and `coverage_flag = missing`, but are
excluded from per-square aggregates rather than receiving an assumed width.

ZIP geography comes from the latest downloaded HUD-USPS ZIP-to-CBSA crosswalk
in `data_raw/hud_usps/`. When a ZIP overlaps multiple CBSAs, the pipeline assigns
the CBSA with the largest residential-address ratio, using total, business, and
other-address ratios as deterministic tie-breakers. City and state come from
HUD; CBSA titles are joined from the BLS labor geography output so material and
labor records use the same area names and codes.

Confirm the log reports `Extracted N products` for every HTML file. If a file
reports `No products extracted`, recapture or update that page before treating
the dated batch as complete.

**How do we collect metal or clay/ceramic roofing?**

Think of each roofing type as a different aisle in the same store:

1. In regular Chrome, use Home Depot's **Building Materials > Roofing** menus
   to find a page containing the actual roof covering. For metal, collect roof
   panels, not flashing or screws. For clay/ceramic, collect clay, terra-cotta,
   or concrete roof tile, not floor tile or decorations.
2. Copy the clean category URL and add it to `config/home_depot_categories.yml`.
   Put the metal URL under `metal_roofing` and the clay/concrete tile URL under
   `roof_tile`.
3. Set the ZIP, wait for prices, scroll through the products, and save the page
   exactly as you did for asphalt. Use the matching names:

   ```text
   metal_roofing_27701_p0.html
   roof_tile_27701_p0.html
   ```

4. Save each page in the same dated folder as the asphalt captures, such as
   `local_pages/2026-07-31/`.
5. Run `src.process_home_depot_batch` once for the dated folder. It discovers
   every configured category from the HTML filenames.
6. Start with one ZIP and one page. Continue to more ZIPs only after the ingest
   reports at least one product and the normalized CSV has sensible coverage
   and price-per-square values.

Metal panels and roof tiles describe coverage differently from shingle bundles.
If their normalized rows say `coverage_flag = missing`, the saved products are
not ready for price comparison yet; the coverage parser must first be taught to
understand panel dimensions or tile pieces-per-square.

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

# Optional automated browser helper. Manual capture in regular Chrome is more
# reliable when Home Depot challenges automated browsers.
# pip install playwright
# playwright install chromium
```

## Pipeline

The batch command performs **ingest → normalize → aggregate** for every HTML
file in one dated directory.

### Capture and process

Open each configured category page in regular Chrome, set the target ZIP/store,
scroll until product cards and prices have loaded, and save it as **Webpage,
Complete**. Keep batches in dated directories and name every file
`<category>_<zip>_p<page>.html`. The category portion must exactly match a key
in `config/home_depot_categories.yml`.

Then process the saved batch:

```powershell
python -m src.process_home_depot_batch --local-html-dir local_pages/2026-07-31
```

Batch options:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--local-html-dir` | required | Dated directory containing all saved material and ZIP pages |
| `--batch-name` | directory name | Explicit output key for a nonstandard source-directory name |
| `--raw-root` | `data_raw/home_depot` | Raw JSONL output root |
| `--intermediate-dir` | `data_intermediate` | Normalized Parquet and CSV directory |
| `--output-root` | `data_output/home_depot` | Aggregate CSV output root |

Outputs for each dated batch:

```
data_raw/home_depot/<batch-name>/YYYY-MM-DD/home_depot_products_raw.jsonl
data_intermediate/home_depot_products_normalized_<batch-name>.parquet
data_intermediate/home_depot_products_normalized_<batch-name>.csv
data_output/home_depot/<batch-name>/home_depot_material_price_*.csv
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
Instead, use one of these routes:

1. **Manual local-HTML ingestion (recommended, zero-cost).** Open the category
  page in regular Chrome, set and verify the ZIP/store, wait for products and
  prices, scroll through the results, and save the page as **Webpage,
  Complete**. Store it in a dated batch directory using the convention
  `<category>_<zip>_pN.html`, then run:

   ```powershell
   python -m src.process_home_depot_batch --local-html-dir local_pages/2026-07-31
   ```

   The saved pages flow through the **identical** extract → normalize →
  aggregate pipeline (`source_method = "local_html"`). Verify that ingestion
  extracts at least one product before continuing.

2. **A managed unblocker / official feed.** Point the fetch step at a licensed
   service (e.g. an enterprise web-unlocker API) or Home Depot's official
   product API/affiliate feed. The parser only needs the page HTML, so any
   sanctioned source can supply it.

Normalization produces a Parquet **and** a sibling CSV with one row per product, including
`price_per_square`, `bulk_price_per_square`, `bulk_discount_pct`,
`material_class`, joined geography, and a `coverage_flag`
(`ok` / `missing` / `suspicious`). Missing coverage is **not** imputed.

Aggregation writes four CSVs (ZIP, state, CBSA, national), each grouped by
`scrape_date × retailer × geography × material_class` with median / p25 / p75 /
min / max price per square, product and store counts, and median bulk metrics.

## Collecting other roofing types

The category keys `metal_roofing` and `roof_tile` already exist in
`config/home_depot_categories.yml`, but their URLs are intentionally blank.
Use the following process to add them.

1. In regular Chrome, navigate through Home Depot's public **Building Materials
   > Roofing** menus to the narrowest listing page that contains field-covering
   products. For metal, target roof panels rather than flashing or accessories.
   For tile, target clay, ceramic/terra-cotta, or concrete roof tiles rather
   than floor tile, siding, or decorative products.
2. Remove optional tracking parameters from the URL, reload it, and confirm it
   still opens the intended category. Add that stable URL to the appropriate
   entry in `config/home_depot_categories.yml`.
3. Choose a consistent ZIP sample from `config/geo_seed_zips.csv`. For every
   ZIP, set and visibly confirm the store/location before saving the page.
4. Save complete pages in a new dated batch directory. Use names such as:

   ```text
   metal_roofing_27701_p0.html
   roof_tile_27701_p0.html
   ```

   If the listing has additional pages, save them as `p1`, `p2`, and so on.
5. Process the dated folder and require a positive extracted-product count for
   each file before expanding the sample:

   ```powershell
   python -m src.process_home_depot_batch --local-html-dir local_pages/2026-07-31
   ```

6. Normalize the pilot and inspect `product_name`, `material_class`,
   `coverage_sqft_per_unit`, `price_per_square`, and `coverage_flag`. The
   existing classifier recognizes corrugated/ribbed metal, standing-seam metal,
   clay/terra-cotta tile, concrete tile, and unspecified tile. Add focused
   classifier tests if actual product wording uses terms not covered by those
   rules.
7. Exclude accessory products and investigate missing or suspicious coverage
   before aggregation. Metal panels often express coverage through panel width
   and length, while tile may use pieces-per-square or pallet coverage; those
   formats may require new coverage parsing before prices can be compared per
   roofing square.

Keep the raw HTML and raw JSONL as audit evidence. Do not overwrite earlier
batches: geography, store selection, product assortment, and price can all
change between collection dates.

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
  or a sanctioned unblocker/official feed (see **Capture and ingest** above).
  The parser, normalizer, classifier, and aggregator are all source-agnostic.
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
