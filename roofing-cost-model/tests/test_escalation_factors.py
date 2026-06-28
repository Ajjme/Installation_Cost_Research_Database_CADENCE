"""Tests for src.build_material_escalation_factors."""

import pandas as pd
import pytest

from src.build_material_escalation_factors import (
    OUTPUT_COLUMNS,
    EscalationError,
    compute_escalation_factors,
)


def _sample_frame() -> pd.DataFrame:
    """Two series; one maps to multiple material classes."""
    return pd.DataFrame(
        [
            # Series A maps to two asphalt classes.
            {
                "series_id": "WPU1361",
                "series_name": "Asphalt Roofing",
                "date": "2024-01-01",
                "value": 200.0,
                "source": "bls",
                "material_mapping": ["asphalt_3_tab", "asphalt_architectural"],
                "use_case": "primary",
                "priority": 1,
                "pulled_at": "2026-06-27T00:00:00Z",
            },
            {
                "series_id": "WPU1361",
                "series_name": "Asphalt Roofing",
                "date": "2024-02-01",
                "value": 220.0,
                "source": "bls",
                "material_mapping": ["asphalt_3_tab", "asphalt_architectural"],
                "use_case": "primary",
                "priority": 1,
                "pulled_at": "2026-06-27T00:00:00Z",
            },
            # Series B maps to a single metal class.
            {
                "series_id": "WPU1017",
                "series_name": "Steel Mill Products",
                "date": "2024-01-01",
                "value": 100.0,
                "source": "bls",
                "material_mapping": ["metal_standing_seam"],
                "use_case": "proxy",
                "priority": 2,
                "pulled_at": "2026-06-27T00:00:00Z",
            },
            {
                "series_id": "WPU1017",
                "series_name": "Steel Mill Products",
                "date": "2024-02-01",
                "value": 150.0,
                "source": "bls",
                "material_mapping": ["metal_standing_seam"],
                "use_case": "proxy",
                "priority": 2,
                "pulled_at": "2026-06-27T00:00:00Z",
            },
        ]
    )


def test_escalation_factor_is_one_in_base_month():
    factors = compute_escalation_factors(_sample_frame(), "2024-01")
    base = factors[factors["month"] == "2024-01"]
    assert not base.empty
    assert (base["escalation_factor"] == 1.0).all()


def test_escalation_factor_values():
    factors = compute_escalation_factors(_sample_frame(), "2024-01")
    feb_asphalt = factors[
        (factors["series_id"] == "WPU1361")
        & (factors["month"] == "2024-02")
        & (factors["material_class"] == "asphalt_3_tab")
    ]
    assert feb_asphalt["escalation_factor"].iloc[0] == pytest.approx(220.0 / 200.0)


def test_missing_base_month_raises_clear_error():
    with pytest.raises(EscalationError) as exc:
        compute_escalation_factors(_sample_frame(), "1999-01")
    assert "1999-01" in str(exc.value)


def test_malformed_base_month_raises():
    with pytest.raises(EscalationError):
        compute_escalation_factors(_sample_frame(), "2024/01")


def test_nonnumeric_values_are_dropped():
    df = _sample_frame()
    # Inject a nonnumeric observation that must be cleaned out.
    df.loc[len(df)] = {
        "series_id": "WPU1361",
        "series_name": "Asphalt Roofing",
        "date": "2024-03-01",
        "value": "not_a_number",
        "source": "bls",
        "material_mapping": ["asphalt_3_tab", "asphalt_architectural"],
        "use_case": "primary",
        "priority": 1,
        "pulled_at": "2026-06-27T00:00:00Z",
    }
    factors = compute_escalation_factors(df, "2024-01")
    assert factors["escalation_factor"].notna().all()
    assert (factors["month"] == "2024-03").sum() == 0


def test_one_series_maps_to_multiple_material_classes():
    factors = compute_escalation_factors(_sample_frame(), "2024-01")
    asphalt_classes = set(
        factors.loc[factors["series_id"] == "WPU1361", "material_class"]
    )
    assert asphalt_classes == {"asphalt_3_tab", "asphalt_architectural"}


def test_output_contains_required_columns():
    factors = compute_escalation_factors(_sample_frame(), "2024-01")
    assert list(factors.columns) == OUTPUT_COLUMNS


def test_comma_string_material_mapping_is_supported():
    df = _sample_frame()
    df["material_mapping"] = df["material_mapping"].map(
        lambda v: ",".join(v) if isinstance(v, list) else v
    )
    factors = compute_escalation_factors(df, "2024-01")
    asphalt_classes = set(
        factors.loc[factors["series_id"] == "WPU1361", "material_class"]
    )
    assert asphalt_classes == {"asphalt_3_tab", "asphalt_architectural"}
