# Roof Labor Productivity Calibration

## Purpose and status

`config/roof_labor_productivity_parameters.csv` is the lookup table that will
connect roof characteristics to BLS occupation wages. The current values are
version 1 calibration seeds. They are assumptions, have low confidence, and
must not be presented as measured productivity until the source and calibration
work is complete.

The table covers new installation only. It excludes tear-off, pitch, stories,
access constraints, geography, damage, and regional productivity effects.

## Units and formula

Rates are person-hours per square foot, not elapsed crew-hours. One minute per
square foot is `1 / 60 = 0.0166667` person-hours per square foot.

For one occupation and one selected scenario:

```text
total_person_hours =
    roof_sqft * base_person_hours_per_sqft + startup_person_hours
```

For example, 1,200 square feet at one minute per square foot is 20 person-hours
before startup time.

The productive rate is intentionally constant across roof-size buckets. Fixed
startup hours create the higher effective rate for smaller roofs without
double-counting size effects.

## Controlled dimensions

- Roof types: three asphalt classes, corrugated and standing-seam metal, clay,
  concrete and unspecified tile, wood shake/shingle, slate, and metal roof tile.
- Shapes: `gable`, `hip`, and `flat`.
- Deck attachments: `6d @ 6"/12"`, `8d @ 6"/12"`, `8d @ 6"/6"`, and
  `6d/8d mix @ 6"/6"`.
- Wall connections: `strap` and `toe_nail`.
- Sizes: small (0-1,499 square feet), medium (1,500-3,000), and large
  (3,001 and above).
- Occupations: the ten OEWS occupations in the repository-level
  `config/target_occupations.csv` file.

All mathematical combinations are retained, including unusual covering and
shape combinations. Future evidence reviews should mark weak combinations as
extrapolated rather than silently deleting them.

## Derivation fields

The generated worksheet exposes each part of the initial derivation:

```text
scenario_total_base_person_hours_per_sqft =
    roof_type_base_person_hours_per_sqft
    * shape_factor
    * deck_attachment_factor
    * wall_connection_factor

base_person_hours_per_sqft =
    scenario_total_base_person_hours_per_sqft * occupation_labor_share

startup_person_hours =
    startup_total_person_hours * occupation_labor_share
```

Occupation shares sum to 1.0 for every roof scenario. The source register in
`config/roof_labor_productivity_sources.csv` describes the evidence behind each
`source_id`. A numeric zero means calibrated no labor; lack of research should
remain provisional or uncalibrated rather than being converted to zero.

## Regeneration

From `roofing-cost-model/` run:

```bash
python -m src.build_labor_productivity_parameters
pytest -q tests/test_labor_productivity_parameters.py
```

The generator writes a stable 7,920-row CSV. Edit assumptions in
`src/build_labor_productivity_parameters.py`, regenerate the worksheet, and
commit the source-register update with the changed calibration.

## Evidence workflow

1. Add each public source to the source register before using it.
2. Record the source's original unit, roof scope, crew size, and limitations.
3. Convert crew-days or roofing squares to person-hours per square foot and
   retain the conversion in the source limitations or calibration notes.
4. Label directly supported values `observed`, calculations from supported
   values `derived`, unsupported judgments `assumption`, and applications to
   unmatched combinations `extrapolated`.
5. Reconcile occupational allocations to the scenario total.
6. Validate representative roofs at 1,499, 1,500, 3,000, and 3,001 square feet.

Claims-adjuster and insurance-sales effort is included because the requested
model includes all ten occupations, although those roles are atypical for a
new-installation productivity model. Their current allocations are assumptions
and should be reviewed before wage-cost integration.