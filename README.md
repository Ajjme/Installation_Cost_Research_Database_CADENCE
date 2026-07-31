# Installation_Cost_Research_Database_CADENCE

This repository supports the CADENCE project's roofing cost research workflow. It contains two main pieces:

1. A Streamlit mapping prototype for labor wages by geography using BLS OEWS data.
2. A roofing material price pipeline that scrapes, normalizes, classifies, and aggregates public retail pricing data, then time-adjusts those prices with national BLS/FRED index series.

The codebase is intentionally split between what is already implemented and what is still being researched. The current implementation is focused on public data sources and auditable transformations. It does not yet include proprietary pricing feeds, permitting data, or a full labor-hours productivity model.

## What is implemented
labor data source
https://www.bls.gov/oes/tables.htm 
### 1. Labor wage mapping prototype

[`mapping_app.py`](mapping_app.py) is a Streamlit app that maps BLS Occupational Employment and Wage Statistics (OEWS) wage data across geographies. It currently:

- Loads MSA, balance-of-state, state, and national Excel tables from `input_data/`.
- Filters to a fixed set of target occupations, including roofers, construction laborers, and first-line supervisors.
- Joins those wages to polygons from the shapefile folder selected in the sidebar.
- Applies a three-level fallback hierarchy when local values are suppressed: MSA/BOS, then state, then national.
- Renders an interactive Plotly choropleth and summary tables for the selected wage metric.

### 2. Roofing material price pipeline

The `roofing-cost-model/` package is a separate, testable pipeline for roofing material pricing. It currently supports:

- Home Depot product capture and local HTML ingestion.
- Normalization of raw product rows into per-square-foot and per-square prices.
- Rule-based material classification for asphalt, metal, and tile products.
- Aggregation to ZIP, state, CBSA, and national levels.
- FRED/BLS index fetching for national time escalation.
- Construction of material escalation factors from those index series.
- RoofVista validation parsing and storage utilities.

## Repository layout

```
Installation_Cost_Research_Database_CADENCE/
	README.md
	mapping_app.py                # Streamlit wage map prototype
	material_cost_analysis.py     # Placeholder for PPI / material index work
	material_readme.md            # Notes for material pricing research
	geo_shapefiles/               # GIS boundary files for the map app
	input_data/                   # Expected input Excel tables for BLS wage mapping
	roofing-cost-model/
		README.md
		config/
		docs/
		src/
		tests/
```

## Data sources and scope

### Labor wages

The map app is built around BLS OEWS data, especially:

- `47-2181` Roofers
- `47-2061` Construction Laborers
- `47-1011` First-Line Supervisors of Construction Trades

The code uses hourly wage metrics such as `H_MEAN`, `H_PCT10`, `H_PCT25`, `H_MEDIAN`, `H_PCT75`, and `H_PCT90` where available.

### Retail material prices

The roofing-cost-model pipeline is designed around public retail prices, starting with Home Depot roofing products. The normalized output includes:

- Retail price per unit
- Coverage per unit
- Price per square foot
- Price per roofing square
- Bulk price and bulk discount where observable
- Material class
- ZIP / city / state / CBSA geography

### Time escalation

National BLS/FRED monthly series are used to build escalation factors for material classes. These are national inflation-style indexes, not local market prices.

### Validation sources

RoofVista support is included as a validation and calibration path for installed-cost benchmarks. It is not the primary material price source.

## Root app usage

### Streamlit map

Run the map from the repository root:

```bash
streamlit run mapping_app.py
```

Expected inputs:

- BLS Excel files in `input_data/` named like `MSA_M2025_dl.xlsx`, `BOS_M2025_dl.xlsx`, `state_M2025_dl.xlsx`, and `national_M2025_dl.xlsx`.
- A shapefile folder, currently expected under `geo_shapefiles/`.

The sidebar lets you choose the occupation and wage metric, plus the shapefile attribute used as the geography key.

### Query-ready labor wage export

Install the root dependencies and generate the wage outputs from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python export_labor_cost_data.py
```

The command writes these files under `data_output/labor/`:

- `labor_wages_wide.parquet`: one row per MSA/BOS and occupation, with all six hourly wage metrics and a source level/source area for each metric.
- `labor_wages_wide.csv`: CSV copy of the wide wage table.
- `labor_wages_long.parquet`: one row per MSA/BOS, occupation, and wage metric for flexible analytical queries.
- `labor_geographies.parquet`: GeoParquet boundary dimension keyed by `AREA`.
- `labor_geography_coverage.csv`: matched, wage-only, and geometry-only area codes.

Every requested metric is resolved independently using local MSA/BOS data, then the area's primary state, then the national value. `SOURCE_LEVEL` is `local`, `state`, `national`, or `unresolved`, and `IS_IMPUTED` identifies state and national substitutions in the long output.

DuckDB can query the Parquet files without importing them into a database:

```sql
-- Direct lookup across all six metric columns.
SELECT AREA_TITLE, OCC_TITLE, H_PCT10, H_MEDIAN, H_MEAN
FROM read_parquet('data_output/labor/labor_wages_wide.parquet')
WHERE OCC_TITLE = 'Roofers'
ORDER BY H_MEAN DESC;

-- Compare fallback levels across metrics.
SELECT WAGE_METRIC, SOURCE_LEVEL, count(*) AS value_count
FROM read_parquet('data_output/labor/labor_wages_long.parquet')
GROUP BY WAGE_METRIC, SOURCE_LEVEL
ORDER BY WAGE_METRIC, SOURCE_LEVEL;
```

For spatial queries, load DuckDB's spatial extension and join geometry only when needed:

```sql
INSTALL spatial;
LOAD spatial;

SELECT wages.AREA_TITLE, wages.OCC_TITLE, wages.H_MEAN, geography.geometry
FROM read_parquet('data_output/labor/labor_wages_wide.parquet') AS wages
JOIN read_parquet('data_output/labor/labor_geographies.parquet') AS geography
	USING (AREA)
WHERE wages.OCC_TITLE = 'Roofers';
```

The wage facts use 2025 OEWS areas, while the available boundary file is from 2019 and contains MSA polygons only. Wage rows are never removed because geometry is unavailable. Review `labor_geography_coverage.csv` before assuming complete map coverage; BOS wage rows intentionally have no polygon in this shapefile.

### Labor escalation projections

The labor escalation pipeline uses the consolidated OEWS workbooks under
`input_data/2021/` through `input_data/2025/` to estimate constrained annual
wage trends and project the resolved 2025 wages through 2050:

```bash
.venv/bin/python export_labor_escalation.py
```

The loader selects cross-industry estimates (`NAICS = 000000`) and classifies
OEWS area types as national (`1`), state or territory (`2` and `3`), MSA (`4`),
and balance-of-state (`6`). Historical values use the same metric-specific
local, state, and national fallback hierarchy as the 2025 wage export.

An annual transition is used only when adjacent years have positive wages and
the same source level and source area. The raw annual rate is the geometric
average of the retained changes. Production rates use hierarchical shrinkage:
local trends are shrunk toward state trends, and state trends are shrunk toward
occupation-national trends. The default historical weight is `n / (n + 4)`,
where `n` is the number of valid annual transitions.

Constrained rates are bounded to the occupation/metric 10th-90th percentile
range and then to `-2%` through `8%` annually. A final documented constraint
prevents projected percentile wages from crossing through 2050. Every applied
adjustment and excluded transition remains available in the audit files.

The command writes:

- `labor_wage_projections_2026_2050.parquet`: one row per area, occupation, and projection year, with 2025 base wages plus raw and constrained factors and projected wages for all six metrics.
- `labor_escalation_rate_audit.parquet`: one row per area, occupation, and metric, with raw and constrained rates, benchmarks, shrinkage weights, bounds, confidence, and adjustment reasons.
- `labor_escalation_history.parquet`: one row per historical annual transition and metric, with endpoint wages, provenance, inclusion status, and exclusion reason.

Example DuckDB lookup:

```sql
SELECT
	AREA_TITLE,
	OCC_TITLE,
	PROJECTION_YEAR,
	H_MEAN_BASE_WAGE,
	H_MEAN_CONSTRAINED_FACTOR,
	H_MEAN_CONSTRAINED_PROJECTED_WAGE
FROM read_parquet(
	'data_output/labor/labor_wage_projections_2026_2050.parquet'
)
WHERE OCC_CODE = '47-2181'
	AND PROJECTION_YEAR IN (2026, 2030, 2040, 2050)
ORDER BY AREA, PROJECTION_YEAR;
```

These values are constrained scenarios extrapolated from four observed annual
changes, not official BLS forecasts. Keep `MODEL_VERSION` with downstream
estimates and rebuild the model when a new OEWS year becomes available.

## Roofing-cost-model usage

From inside `roofing-cost-model/`:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Common pipeline commands:

```bash
# Fetch FRED/BLS index series
python -m src.fetch_fred_indexes

# Build material escalation factors
python -m src.build_material_escalation_factors --base-month 2024-01

# Normalize raw Home Depot product rows
python -m src.normalize_products --input data_raw/home_depot/YYYY-MM-DD/home_depot_products_raw.jsonl

# Aggregate normalized prices to ZIP / state / CBSA / national outputs
python -m src.aggregate_prices --input data_intermediate/home_depot_products_normalized.parquet --out-dir data_output
```

The package README in [`roofing-cost-model/README.md`](roofing-cost-model/README.md) has the full Home Depot capture workflow, including the local-HTML fallback that is currently the most reliable path for public page ingestion.

## Current limitations

- Live Home Depot scraping is constrained by anti-bot protection. The supported path is guided browser capture or local HTML ingestion.
- `material_cost_analysis.py` is currently only a placeholder for future index-based material analysis.
- The repository does not yet calculate roofing labor-hours by roof size, damage fraction, or crew composition. That remains a separate research/modeling task.

## Testing

The implemented roofing-cost-model components are covered by unit tests for classification, normalization, escalation factors, and RoofVista parsing.

```bash
cd roofing-cost-model
pytest -q
```

## Research notes

The longer-term model design is to combine:

- Public retail material pricing by geography.
- BLS wage data for labor baselines.
- Separate productivity assumptions for labor hours by roof size and damage level.
- Validation/calibration against RoofVista or similar quote sources when available.

That architecture keeps retail material pricing, labor wages, and installed-cost calibration as separate layers instead of mixing them into one opaque estimate.
