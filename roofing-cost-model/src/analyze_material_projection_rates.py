"""Estimate projection-ready material escalation rates from historical factors.

The analysis uses the primary (priority 1) national index for each material,
calculates month-over-month changes, removes unusually large shifts, and
computes geometric average rates suitable for compounding in later models.

Usage:
    python -m src.analyze_material_projection_rates

Outputs:
    data_output/material_projection_rates.csv
    data_output/material_projection_excluded_changes.csv
"""

from __future__ import annotations

import argparse
import logging
import math
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_INPUT_PATH = _PROJECT_ROOT / "data_output" / "material_escalation_factors.parquet"
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "data_output" / "material_projection_rates.csv"
DEFAULT_EXCLUDED_PATH = (
    _PROJECT_ROOT / "data_output" / "material_projection_excluded_changes.csv"
)

REQUIRED_COLUMNS = {
    "material_class",
    "series_id",
    "series_name",
    "date",
    "index_value",
    "priority",
}

SUMMARY_COLUMNS = [
    "material_class",
    "series_id",
    "series_name",
    "history_start",
    "history_end",
    "monthly_changes_total",
    "monthly_changes_used",
    "outliers_removed",
    "arithmetic_mean_monthly_change",
    "geometric_mean_monthly_change",
    "annual_escalation_factor",
    "annualized_escalation_rate",
    "monthly_change_std_dev",
    "outlier_z_threshold",
    "minimum_outlier_change",
]

EXCLUDED_COLUMNS = [
    "material_class",
    "series_id",
    "date",
    "previous_index_value",
    "index_value",
    "monthly_change",
    "modified_z_score",
]


class ProjectionRateError(ValueError):
    """Raised when historical factors cannot produce projection rates."""


def _validate_parameters(z_threshold: float, minimum_outlier_change: float) -> None:
    if z_threshold <= 0:
        raise ProjectionRateError("z_threshold must be greater than zero.")
    if not 0 <= minimum_outlier_change < 1:
        raise ProjectionRateError(
            "minimum_outlier_change must be between 0 and 1."
        )


def _modified_z_scores(changes: pd.Series) -> pd.Series:
    """Return absolute modified z-scores based on median absolute deviation."""
    median = changes.median()
    deviations = (changes - median).abs()
    median_absolute_deviation = deviations.median()

    if median_absolute_deviation == 0:
        scores = pd.Series(0.0, index=changes.index)
        scores.loc[deviations.gt(0)] = float("inf")
        return scores

    return 0.67448975 * deviations / median_absolute_deviation


def analyze_projection_rates(
    factors: pd.DataFrame,
    *,
    z_threshold: float = 3.5,
    minimum_outlier_change: float = 0.05,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate robust average changes for each primary material index.

    A monthly change is excluded only when its absolute modified z-score is
    above ``z_threshold`` and its absolute size is at least
    ``minimum_outlier_change``. Average rates are geometric so they can be
    compounded without the bias introduced by arithmetic return averages.
    """
    _validate_parameters(z_threshold, minimum_outlier_change)

    if factors is None or factors.empty:
        raise ProjectionRateError("Escalation-factor input is empty.")

    missing = REQUIRED_COLUMNS.difference(factors.columns)
    if missing:
        raise ProjectionRateError(
            f"Escalation-factor input is missing columns: {', '.join(sorted(missing))}."
        )

    primary = factors.copy()
    primary["priority"] = pd.to_numeric(primary["priority"], errors="coerce")
    primary["date"] = pd.to_datetime(primary["date"], errors="coerce")
    primary["index_value"] = pd.to_numeric(
        primary["index_value"], errors="coerce"
    )
    primary = primary.loc[primary["priority"].eq(1)].dropna(
        subset=["material_class", "series_id", "date", "index_value"]
    )
    primary = primary.loc[primary["index_value"].gt(0)].copy()
    primary = primary.sort_values(["material_class", "date"])

    if primary.empty:
        raise ProjectionRateError("No usable priority 1 material indexes were found.")

    duplicate_keys = primary.duplicated(["material_class", "date"], keep=False)
    if duplicate_keys.any():
        materials = sorted(primary.loc[duplicate_keys, "material_class"].unique())
        raise ProjectionRateError(
            "Multiple primary observations exist for the same material and month: "
            + ", ".join(materials)
        )

    series_counts = primary.groupby("material_class")["series_id"].nunique()
    ambiguous_materials = sorted(series_counts[series_counts.gt(1)].index)
    if ambiguous_materials:
        raise ProjectionRateError(
            "Multiple primary series exist for material classes: "
            + ", ".join(ambiguous_materials)
        )

    primary["previous_index_value"] = primary.groupby("material_class")[
        "index_value"
    ].shift()
    primary["monthly_change"] = primary.groupby("material_class")[
        "index_value"
    ].pct_change(fill_method=None)
    changes = primary.dropna(subset=["monthly_change"]).copy()

    if changes.empty:
        raise ProjectionRateError(
            "At least two observations per material are needed to calculate changes."
        )

    changes["modified_z_score"] = changes.groupby("material_class")[
        "monthly_change"
    ].transform(_modified_z_scores)
    changes["is_outlier"] = changes["modified_z_score"].gt(
        z_threshold
    ) & changes["monthly_change"].abs().ge(minimum_outlier_change)

    summary_rows: list[dict[str, object]] = []
    for material_class, group in changes.groupby("material_class", sort=True):
        retained = group.loc[~group["is_outlier"]]
        if retained.empty:
            raise ProjectionRateError(
                f"Outlier filtering removed every change for {material_class}."
            )

        geometric_monthly_change = math.exp(
            retained["monthly_change"].map(math.log1p).mean()
        ) - 1
        annual_escalation_factor = (1 + geometric_monthly_change) ** 12
        first = group.iloc[0]

        summary_rows.append(
            {
                "material_class": material_class,
                "series_id": first["series_id"],
                "series_name": first["series_name"],
                "history_start": primary.loc[
                    primary["material_class"].eq(material_class), "date"
                ].min(),
                "history_end": group["date"].max(),
                "monthly_changes_total": len(group),
                "monthly_changes_used": len(retained),
                "outliers_removed": int(group["is_outlier"].sum()),
                "arithmetic_mean_monthly_change": retained["monthly_change"].mean(),
                "geometric_mean_monthly_change": geometric_monthly_change,
                "annual_escalation_factor": annual_escalation_factor,
                "annualized_escalation_rate": annual_escalation_factor - 1,
                "monthly_change_std_dev": retained["monthly_change"].std(),
                "outlier_z_threshold": z_threshold,
                "minimum_outlier_change": minimum_outlier_change,
            }
        )

    summary = pd.DataFrame(summary_rows, columns=SUMMARY_COLUMNS)
    excluded = changes.loc[changes["is_outlier"], EXCLUDED_COLUMNS].reset_index(
        drop=True
    )
    return summary, excluded


def run(
    *,
    input_path: Path = DEFAULT_INPUT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    excluded_path: Path = DEFAULT_EXCLUDED_PATH,
    z_threshold: float = 3.5,
    minimum_outlier_change: float = 0.05,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load historical factors, calculate rates, and write CSV outputs."""
    input_path = Path(input_path)
    if not input_path.is_file():
        raise ProjectionRateError(f"Escalation-factor file not found: {input_path}")

    factors = pd.read_parquet(input_path)
    summary, excluded = analyze_projection_rates(
        factors,
        z_threshold=z_threshold,
        minimum_outlier_change=minimum_outlier_change,
    )

    output_path = Path(output_path)
    excluded_path = Path(excluded_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    excluded_path.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output_path, index=False)
    excluded.to_csv(excluded_path, index=False)
    logger.info("Wrote %d material projection rates to %s", len(summary), output_path)
    logger.info("Wrote %d excluded historical changes to %s", len(excluded), excluded_path)
    return summary, excluded


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Estimate robust material escalation rates for future projections."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT_PATH))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--excluded-output", default=str(DEFAULT_EXCLUDED_PATH))
    parser.add_argument("--z-threshold", type=float, default=3.5)
    parser.add_argument(
        "--minimum-outlier-change",
        type=float,
        default=0.05,
        help="Minimum absolute monthly change eligible for exclusion (default: 0.05).",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    run(
        input_path=Path(args.input),
        output_path=Path(args.output),
        excluded_path=Path(args.excluded_output),
        z_threshold=args.z_threshold,
        minimum_outlier_change=args.minimum_outlier_change,
    )


if __name__ == "__main__":
    main()