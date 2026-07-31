import pandas as pd
import pytest

from labor_escalation import (
    build_projection_table,
    build_transition_audit,
    constrain_rates,
    enforce_percentile_rate_order,
    summarize_raw_rates,
)


def sample_panel():
    rows = []
    for year, wage, source in [
        (2021, 20.0, "local"),
        (2022, 21.0, "local"),
        (2023, 22.05, "local"),
        (2024, 30.0, "state"),
        (2025, 31.5, "state"),
    ]:
        rows.append(
            {
                "AREA": "0012345",
                "AREA_TITLE": "Example",
                "PRIM_STATE": "AL",
                "GEOGRAPHY_TYPE": "msa",
                "OCC_CODE": "47-2181",
                "OCC_TITLE": "Roofers",
                "DATA_YEAR": year,
                "WAGE_METRIC": "H_MEAN",
                "HOURLY_WAGE": wage,
                "SOURCE_LEVEL": source,
                "SOURCE_AREA": "0012345" if source == "local" else "0000001",
            }
        )
    return pd.DataFrame(rows)


def test_source_change_is_excluded_from_raw_rate():
    transitions = build_transition_audit(sample_panel())
    rates = summarize_raw_rates(transitions)

    assert transitions["IS_INCLUDED"].tolist() == [True, True, False, True]
    assert transitions.loc[~transitions["IS_INCLUDED"], "EXCLUSION_REASON"].tolist() == [
        "source_changed"
    ]
    assert rates["VALID_TRANSITIONS"].iloc[0] == 3
    assert rates["RAW_RATE"].iloc[0] == pytest.approx(0.05)
    assert rates["EXCLUSION_REASONS"].iloc[0] == "source_changed"


def test_hierarchical_rate_is_shrunk_and_hard_bounded():
    raw = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "PRIM_STATE": "AL",
                "OCC_CODE": "47-2181",
                "WAGE_METRIC": "H_MEAN",
                "TRANSITIONS_TOTAL": 4,
                "VALID_TRANSITIONS": 4,
                "EXCLUDED_TRANSITIONS": 0,
                "RAW_RATE": 0.50,
                "LOG_CHANGE_STD_DEV": 0.01,
            },
            {
                "AREA": "0067890",
                "PRIM_STATE": "AL",
                "OCC_CODE": "47-2181",
                "WAGE_METRIC": "H_MEAN",
                "TRANSITIONS_TOTAL": 4,
                "VALID_TRANSITIONS": 4,
                "EXCLUDED_TRANSITIONS": 0,
                "RAW_RATE": 0.04,
                "LOG_CHANGE_STD_DEV": 0.01,
            },
        ]
    )
    state = pd.DataFrame(
        [{"PRIM_STATE": "AL", "OCC_CODE": "47-2181", "WAGE_METRIC": "H_MEAN", "RAW_RATE": 0.04, "VALID_TRANSITIONS": 4}]
    )
    national = pd.DataFrame(
        [{"OCC_CODE": "47-2181", "WAGE_METRIC": "H_MEAN", "RAW_RATE": 0.03, "VALID_TRANSITIONS": 4}]
    )

    audit = constrain_rates(raw, state, national)
    extreme = audit[audit["AREA"] == "0012345"].iloc[0]

    assert extreme["CONSTRAINED_RATE"] <= 0.08
    assert extreme["CONSTRAINED_RATE"] < extreme["RAW_RATE"]
    assert "shrunk_to_state" in extreme["ADJUSTMENT_REASONS"]
    assert "hard_cap" in extreme["ADJUSTMENT_REASONS"]


def test_national_rate_is_used_when_state_rate_is_missing():
    raw = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "PRIM_STATE": "WY",
                "OCC_CODE": "47-2181",
                "WAGE_METRIC": "H_MEAN",
                "TRANSITIONS_TOTAL": 4,
                "VALID_TRANSITIONS": 3,
                "EXCLUDED_TRANSITIONS": 1,
                "RAW_RATE": 0.05,
                "LOG_CHANGE_STD_DEV": 0.01,
            }
        ]
    )
    state = pd.DataFrame(
        columns=["PRIM_STATE", "OCC_CODE", "WAGE_METRIC", "RAW_RATE", "VALID_TRANSITIONS"]
    )
    national = pd.DataFrame(
        [{"OCC_CODE": "47-2181", "WAGE_METRIC": "H_MEAN", "RAW_RATE": 0.03, "VALID_TRANSITIONS": 4}]
    )

    audit = constrain_rates(raw, state, national)

    assert audit["NATIONAL_RATE"].iloc[0] == pytest.approx(0.03)
    assert pd.notna(audit["CONSTRAINED_RATE"].iloc[0])
    assert "state_raw_unavailable" in audit["ADJUSTMENT_REASONS"].iloc[0]


def test_projection_compounds_constrained_rate():
    base = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "AREA_TITLE": "Example",
                "PRIM_STATE": "AL",
                "GEOGRAPHY_TYPE": "msa",
                "OCC_CODE": "47-2181",
                "OCC_TITLE": "Roofers",
                **{metric: 20.0 for metric in ["H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90", "H_MEAN"]},
            }
        ]
    )
    audit = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "OCC_CODE": "47-2181",
                "WAGE_METRIC": metric,
                "RAW_RATE": 0.04,
                "CONSTRAINED_RATE": 0.03,
            }
            for metric in ["H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90", "H_MEAN"]
        ]
    )

    projections = build_projection_table(base, audit, projection_start=2026, projection_end=2050)

    assert len(projections) == 25
    assert projections.iloc[0]["H_MEAN_CONSTRAINED_FACTOR"] == pytest.approx(1.03)
    assert projections.iloc[-1]["H_MEAN_CONSTRAINED_FACTOR"] == pytest.approx(1.03**25)
    assert projections.iloc[-1]["H_MEAN_CONSTRAINED_PROJECTED_WAGE"] == pytest.approx(
        20 * 1.03**25
    )


def test_percentile_rates_are_adjusted_to_prevent_crossing():
    metrics = ["H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90"]
    base = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "OCC_CODE": "47-2181",
                "H_PCT10": 20.0,
                "H_PCT25": 21.0,
                "H_MEDIAN": 22.0,
                "H_PCT75": 23.0,
                "H_PCT90": 24.0,
            }
        ]
    )
    audit = pd.DataFrame(
        [
            {
                "AREA": "0012345",
                "OCC_CODE": "47-2181",
                "WAGE_METRIC": metric,
                "CONSTRAINED_RATE": 0.08 if metric == "H_PCT10" else 0.02,
                "ADJUSTMENT_REASONS": "shrunk_to_state",
            }
            for metric in metrics
        ]
    )

    ordered = enforce_percentile_rate_order(audit, base, 25)
    rates = ordered.set_index("WAGE_METRIC")["CONSTRAINED_RATE"]
    projected = {
        metric: base.iloc[0][metric] * (1 + rates[metric]) ** 25 for metric in metrics
    }

    for lower, upper in zip(metrics, metrics[1:]):
        assert projected[lower] <= projected[upper] + 1e-9
    assert rates["H_PCT25"] > 0.02
    reasons = ordered.set_index("WAGE_METRIC").loc["H_PCT25", "ADJUSTMENT_REASONS"]
    assert "percentile_order_constraint" in reasons