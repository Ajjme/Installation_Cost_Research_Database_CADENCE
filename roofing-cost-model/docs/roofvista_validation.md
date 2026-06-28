# RoofVista Validation Dataset

## Purpose
This dataset is a validation source for installed roofing cost levels across geography and material tiers. It is not the primary retail-material pricing model.

Our primary model is built from retailer data (Home Depot/Lowe's). RoofVista estimates are used to compare installed cost levels and infer reasonableness of material/labor splits.

## API Endpoint
- Base endpoint: https://roofvista.com/api/v1/public/pricing
- Example: https://roofvista.com/api/v1/public/pricing?state=MA
- Example: https://roofvista.com/api/v1/public/pricing?state=CT&material=architectural

## Supported States
Current pipeline defaults:
- MA
- CT
- RI
- NH
- VT
- ME

## Material Tiers Attempted
- 3-tab shingles
- architectural shingles
- premium shingles
- tile
- standing seam metal

The client attempts multiple material-parameter aliases for each tier and logs failures without stopping the run.

## Sample Location Generation
Input file: config/roofvista_sample_locations.csv

Required columns:
- sample_id
- state
- city
- zip_code
- address
- latitude
- longitude
- source

The pipeline samples locations per state using a fixed random seed (`--random-seed`, default 42) and a configurable size (`--sample-size`, default 25).

If no rows are available for a state, fallback city/ZIP seeds are used.

## Schema Discovery
Run schema discovery first to inspect response shape before relying on normalized fields:

```bash
python src/fetch_roofvista_validation.py --discover-schema
```

Outputs are written to:
- data_raw/roofvista/schema_discovery/

Logs include top-level keys, material-like values, cost-like fields, and effective-date-like fields.

## Full Validation Pull
Run the full pull:

```bash
python src/fetch_roofvista_validation.py --sample-size 25
```

Optional filters:

```bash
python src/fetch_roofvista_validation.py --states MA CT RI NH VT ME
python src/fetch_roofvista_validation.py --materials architectural premium tile
```

Outputs:
- SQLite DB: data_output/roofvista/roofvista_validation.sqlite
- CSV: data_output/roofvista/roofvista_validation_estimates.csv
- Parquet: data_output/roofvista/roofvista_validation_estimates.parquet

Raw payloads are preserved under:
- data_raw/roofvista/responses/

## SQLite Tables
- roofvista_runs
- roofvista_sample_locations
- roofvista_raw_responses
- roofvista_normalized_estimates

## Known Limitations
- This is an installed-cost validation source, not a pure material-only retail price source.
- If the API only returns state-level estimates, sampled locations are placeholders and results are duplicated across samples with `geographic_resolution = "state"`.
- API response schema may evolve; parser preserves raw responses and avoids hard-coded strict schemas.
