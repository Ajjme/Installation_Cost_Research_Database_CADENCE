"""Tests for the roof labor productivity calibration worksheet."""

from pathlib import Path

import pandas as pd
import pytest

from src.build_labor_productivity_parameters import (
    DECK_ATTACHMENTS,
    OUTPUT_COLUMNS,
    ROOF_SHAPES,
    ROOF_TYPES,
    SIZE_BUCKETS,
    TARGET_OCCUPATION_CODES,
    TARGET_OCCUPATIONS,
    WALL_CONNECTIONS,
    build_parameters,
    calculate_total_person_hours,
)


KEY_COLUMNS = [
    "roof_type",
    "roof_shape",
    "roof_deck_attachment",
    "roof_wall_connection",
    "size_bucket",
    "occupation",
]
MODEL_ROOT = Path(__file__).resolve().parents[1]


def test_builds_complete_unique_cross_product():
    parameters = build_parameters()
    expected_rows = (
        len(ROOF_TYPES)
        * len(ROOF_SHAPES)
        * len(DECK_ATTACHMENTS)
        * len(WALL_CONNECTIONS)
        * len(SIZE_BUCKETS)
        * len(TARGET_OCCUPATIONS)
    )

    assert expected_rows == 7920
    assert len(parameters) == expected_rows
    assert not parameters.duplicated(KEY_COLUMNS).any()
    assert list(parameters.columns) == OUTPUT_COLUMNS
    assert parameters.set_index("occupation")["occupation_code"].to_dict() == (
        TARGET_OCCUPATION_CODES
    )


def test_base_rates_do_not_change_by_size_bucket():
    parameters = build_parameters()
    scenario_columns = [column for column in KEY_COLUMNS if column != "size_bucket"]

    distinct_rates = parameters.groupby(scenario_columns)[
        "base_person_hours_per_sqft"
    ].nunique()

    assert (distinct_rates == 1).all()


def test_occupation_allocations_reconcile_to_scenario_totals():
    parameters = build_parameters()
    scenario_columns = [column for column in KEY_COLUMNS if column != "occupation"]
    totals = parameters.groupby(scenario_columns, dropna=False).agg(
        occupation_share=("occupation_labor_share", "sum"),
        allocated_rate=("base_person_hours_per_sqft", "sum"),
        scenario_rate=("scenario_total_base_person_hours_per_sqft", "first"),
        allocated_startup=("startup_person_hours", "sum"),
        scenario_startup=("startup_total_person_hours", "first"),
    )

    assert totals["occupation_share"].to_numpy() == pytest.approx(1.0)
    assert totals["allocated_rate"].to_numpy() == pytest.approx(
        totals["scenario_rate"].to_numpy(), abs=1e-7
    )
    assert totals["allocated_startup"].to_numpy() == pytest.approx(
        totals["scenario_startup"].to_numpy()
    )


def test_size_boundaries_are_contiguous_and_startup_declines():
    parameters = build_parameters()
    sizes = (
        parameters[["size_bucket", "min_roof_sqft", "max_roof_sqft"]]
        .drop_duplicates()
        .sort_values("min_roof_sqft")
        .reset_index(drop=True)
    )

    assert sizes["size_bucket"].tolist() == ["small", "medium", "large"]
    assert sizes.loc[0, "max_roof_sqft"] + 1 == sizes.loc[1, "min_roof_sqft"]
    assert sizes.loc[1, "max_roof_sqft"] + 1 == sizes.loc[2, "min_roof_sqft"]
    assert pd.isna(sizes.loc[2, "max_roof_sqft"])

    startup = parameters.groupby("size_bucket")["startup_total_person_hours"].first()
    assert startup["small"] > startup["medium"] > startup["large"]


def test_generated_csv_matches_builder_and_source_register():
    generated = pd.read_csv(
        MODEL_ROOT / "config" / "roof_labor_productivity_parameters.csv"
    )
    expected = build_parameters()
    pd.testing.assert_frame_equal(generated, expected, check_dtype=False)

    sources = pd.read_csv(MODEL_ROOT / "config" / "roof_labor_productivity_sources.csv")
    assert set(generated["source_id"]).issubset(set(sources["source_id"]))


def test_one_minute_per_sqft_converts_to_twenty_hours_for_1200_sqft():
    one_minute_in_hours = 1 / 60
    assert calculate_total_person_hours(1200, one_minute_in_hours) == pytest.approx(20.0)


def test_negative_roof_area_is_rejected():
    with pytest.raises(ValueError, match="nonnegative"):
        calculate_total_person_hours(-1, 0.016667)