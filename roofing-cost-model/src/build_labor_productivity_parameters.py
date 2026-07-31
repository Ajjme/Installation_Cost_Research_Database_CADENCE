"""Build the exhaustive roof labor productivity calibration worksheet."""

from __future__ import annotations

import argparse
from itertools import product
from pathlib import Path

import pandas as pd


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL_ROOT = Path(__file__).resolve().parents[1]
TARGET_OCCUPATIONS_PATH = REPOSITORY_ROOT / "config" / "target_occupations.csv"
_target_occupations = pd.read_csv(TARGET_OCCUPATIONS_PATH, dtype="string")
TARGET_OCCUPATION_CODES = dict(
    zip(_target_occupations["occupation"], _target_occupations["occupation_code"])
)
TARGET_OCCUPATIONS = tuple(TARGET_OCCUPATION_CODES)

ROOF_TYPES = (
    "asphalt_3_tab",
    "asphalt_architectural",
    "asphalt_premium_architectural",
    "metal_corrugated_panel",
    "metal_standing_seam",
    "tile_clay",
    "tile_concrete",
    "tile_unspecified",
    "wood_shake_shingle",
    "slate",
    "metal_roof_tile",
)

ROOF_SHAPES = ("gable", "hip", "flat")

DECK_ATTACHMENTS = {
    "6d_6in_12in": '6d @ 6"/12"',
    "8d_6in_12in": '8d @ 6"/12"',
    "8d_6in_6in": '8d @ 6"/6"',
    "6d_8d_mix_6in_6in": '6d/8d mix @ 6"/6"',
}

WALL_CONNECTIONS = ("strap", "toe_nail")

SIZE_BUCKETS = (
    ("small", 0, 1499),
    ("medium", 1500, 3000),
    ("large", 3001, None),
)

OUTPUT_COLUMNS = [
    "roof_type",
    "roof_shape",
    "roof_deck_attachment",
    "roof_deck_attachment_label",
    "roof_wall_connection",
    "size_bucket",
    "min_roof_sqft",
    "max_roof_sqft",
    "occupation_code",
    "occupation",
    "roof_type_base_person_hours_per_sqft",
    "shape_factor",
    "deck_attachment_factor",
    "wall_connection_factor",
    "occupation_labor_share",
    "scenario_total_base_person_hours_per_sqft",
    "base_person_hours_per_sqft",
    "startup_total_person_hours",
    "startup_person_hours",
    "source_id",
    "evidence_type",
    "confidence",
    "calibration_status",
    "notes",
]

# Version 1 values are transparent calibration seeds, not measured productivity.
BASE_PERSON_HOURS_PER_SQFT = {
    "asphalt_3_tab": 0.025,
    "asphalt_architectural": 0.030,
    "asphalt_premium_architectural": 0.035,
    "metal_corrugated_panel": 0.035,
    "metal_standing_seam": 0.050,
    "tile_clay": 0.070,
    "tile_concrete": 0.065,
    "tile_unspecified": 0.0675,
    "wood_shake_shingle": 0.060,
    "slate": 0.100,
    "metal_roof_tile": 0.055,
}

SHAPE_FACTORS = {"gable": 1.0, "hip": 1.15, "flat": 0.90}
DECK_ATTACHMENT_FACTORS = {
    "6d_6in_12in": 1.0,
    "8d_6in_12in": 1.05,
    "8d_6in_6in": 1.12,
    "6d_8d_mix_6in_6in": 1.15,
}
WALL_CONNECTION_FACTORS = {"toe_nail": 1.0, "strap": 1.08}

OCCUPATION_SHARES = {
    "Purchasing Managers": 0.02,
    "Construction Managers": 0.05,
    "Claims Adjusters, Examiners, and Investigators": 0.01,
    "Cost Estimators": 0.03,
    "Insurance Sales Agents": 0.01,
    "Construction Laborers": 0.20,
    "Roofers": 0.55,
    "Construction and Building Inspectors": 0.02,
    "Installation, Maintenance, and Repair Occupations": 0.04,
    "First-Line Supervisors of Construction Trades": 0.07,
}

STARTUP_TOTAL_PERSON_HOURS = {"small": 24.0, "medium": 16.0, "large": 12.0}


def build_parameters() -> pd.DataFrame:
    """Return one deterministic calibration row per complete scenario and occupation."""
    rows = []
    dimensions = product(
        ROOF_TYPES,
        ROOF_SHAPES,
        DECK_ATTACHMENTS,
        WALL_CONNECTIONS,
        SIZE_BUCKETS,
        TARGET_OCCUPATIONS,
    )
    for roof_type, roof_shape, deck_attachment, wall_connection, size, occupation in dimensions:
        size_bucket, min_roof_sqft, max_roof_sqft = size
        occupation_share = OCCUPATION_SHARES[occupation]
        scenario_rate = (
            BASE_PERSON_HOURS_PER_SQFT[roof_type]
            * SHAPE_FACTORS[roof_shape]
            * DECK_ATTACHMENT_FACTORS[deck_attachment]
            * WALL_CONNECTION_FACTORS[wall_connection]
        )
        rows.append(
            {
                "roof_type": roof_type,
                "roof_shape": roof_shape,
                "roof_deck_attachment": deck_attachment,
                "roof_deck_attachment_label": DECK_ATTACHMENTS[deck_attachment],
                "roof_wall_connection": wall_connection,
                "size_bucket": size_bucket,
                "min_roof_sqft": min_roof_sqft,
                "max_roof_sqft": max_roof_sqft,
                "occupation_code": TARGET_OCCUPATION_CODES[occupation],
                "occupation": occupation,
                "roof_type_base_person_hours_per_sqft": BASE_PERSON_HOURS_PER_SQFT[
                    roof_type
                ],
                "shape_factor": SHAPE_FACTORS[roof_shape],
                "deck_attachment_factor": DECK_ATTACHMENT_FACTORS[deck_attachment],
                "wall_connection_factor": WALL_CONNECTION_FACTORS[wall_connection],
                "occupation_labor_share": occupation_share,
                "scenario_total_base_person_hours_per_sqft": round(scenario_rate, 8),
                "base_person_hours_per_sqft": round(scenario_rate * occupation_share, 8),
                "startup_total_person_hours": STARTUP_TOTAL_PERSON_HOURS[size_bucket],
                "startup_person_hours": round(
                    STARTUP_TOTAL_PERSON_HOURS[size_bucket] * occupation_share, 4
                ),
                "source_id": "ASSUMPTION_V1",
                "evidence_type": "assumption",
                "confidence": "low",
                "calibration_status": "provisional",
                "notes": "Initial seed; replace or calibrate against documented evidence.",
            }
        )

    return pd.DataFrame(rows, columns=OUTPUT_COLUMNS).sort_values(
        [
            "roof_type",
            "roof_shape",
            "roof_deck_attachment",
            "roof_wall_connection",
            "min_roof_sqft",
            "occupation",
        ],
        ignore_index=True,
    )


def calculate_total_person_hours(
    roof_sqft: float,
    base_person_hours_per_sqft: float,
    startup_person_hours: float = 0.0,
) -> float:
    """Calculate total person-hours for one occupation and roof scenario."""
    if roof_sqft < 0:
        raise ValueError("roof_sqft must be nonnegative")
    return roof_sqft * base_person_hours_per_sqft + startup_person_hours


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=MODEL_ROOT / "config" / "roof_labor_productivity_parameters.csv",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    parameters = build_parameters()
    parameters.to_csv(args.output, index=False)
    print(f"Wrote {len(parameters):,} rows to {args.output}")


if __name__ == "__main__":
    main()