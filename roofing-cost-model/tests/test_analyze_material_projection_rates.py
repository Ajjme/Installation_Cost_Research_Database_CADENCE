"""Tests for src.analyze_material_projection_rates."""

import pandas as pd
import pytest

from src.analyze_material_projection_rates import (
    ProjectionRateError,
    analyze_projection_rates,
)


def _sample_factors() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=5, freq="MS")
    asphalt_values = [100.0, 101.0, 102.01, 153.015, 154.54515]
    rows = []
    for date, value in zip(dates, asphalt_values):
        rows.append(
            {
                "material_class": "asphalt_3_tab",
                "series_id": "ASPHALT",
                "series_name": "Asphalt primary",
                "date": date,
                "index_value": value,
                "priority": 1,
            }
        )
        rows.append(
            {
                "material_class": "asphalt_3_tab",
                "series_id": "SECONDARY",
                "series_name": "Secondary proxy",
                "date": date,
                "index_value": value * 2,
                "priority": 2,
            }
        )
    return pd.DataFrame(rows)


def test_large_monthly_shift_is_removed_from_geometric_average():
    summary, excluded = analyze_projection_rates(_sample_factors())

    row = summary.iloc[0]
    assert row["monthly_changes_total"] == 4
    assert row["monthly_changes_used"] == 3
    assert row["outliers_removed"] == 1
    assert row["geometric_mean_monthly_change"] == pytest.approx(0.01)
    assert row["annual_escalation_factor"] == pytest.approx(1.01**12)
    assert row["annualized_escalation_rate"] == pytest.approx(1.01**12 - 1)
    assert excluded["monthly_change"].iloc[0] == pytest.approx(0.5)


def test_only_primary_series_is_used():
    summary, _ = analyze_projection_rates(_sample_factors())

    assert summary["series_id"].tolist() == ["ASPHALT"]


def test_change_below_absolute_floor_is_retained():
    factors = _sample_factors()
    primary_mask = factors["priority"].eq(1)
    factors.loc[primary_mask, "index_value"] = [100.0, 101.0, 102.01, 106.0904, 107.151304]

    summary, excluded = analyze_projection_rates(factors)

    assert excluded.empty
    assert summary["monthly_changes_used"].iloc[0] == 4


def test_duplicate_primary_month_raises_clear_error():
    factors = _sample_factors()
    duplicate = factors.iloc[[0]].copy()

    with pytest.raises(ProjectionRateError, match="Multiple primary observations"):
        analyze_projection_rates(pd.concat([factors, duplicate], ignore_index=True))


def test_invalid_parameters_raise_clear_error():
    with pytest.raises(ProjectionRateError, match="z_threshold"):
        analyze_projection_rates(_sample_factors(), z_threshold=0)

    with pytest.raises(ProjectionRateError, match="minimum_outlier_change"):
        analyze_projection_rates(
            _sample_factors(), minimum_outlier_change=1.0
        )