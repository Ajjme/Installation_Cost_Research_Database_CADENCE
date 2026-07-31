from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from labor_cost_data import WAGE_METRICS, read_annual_oews_files
from labor_escalation import build_labor_escalation_outputs


DEFAULT_BASE_WAGES = Path("data_output/labor/labor_wages_wide.parquet")
DEFAULT_OUTPUT_DIR = Path("data_output/labor")


def validate_outputs(
    projections: pd.DataFrame,
    audit: pd.DataFrame,
    history: pd.DataFrame,
    base_wages: pd.DataFrame,
    projection_start: int,
    projection_end: int,
) -> dict[str, int]:
    expected_audit = len(base_wages) * len(WAGE_METRICS)
    expected_projections = len(base_wages) * (projection_end - projection_start + 1)
    if len(audit) != expected_audit:
        raise RuntimeError(f"Rate audit has {len(audit):,} rows; expected {expected_audit:,}")
    if len(projections) != expected_projections:
        raise RuntimeError(
            f"Projection output has {len(projections):,} rows; expected {expected_projections:,}"
        )
    if audit.duplicated(["AREA", "OCC_CODE", "WAGE_METRIC"]).any():
        raise RuntimeError("Rate audit contains duplicate area/occupation/metric keys")
    if projections.duplicated(["AREA", "OCC_CODE", "PROJECTION_YEAR"]).any():
        raise RuntimeError("Projection output contains duplicate area/occupation/year keys")
    if history.duplicated(
        ["AREA", "OCC_CODE", "WAGE_METRIC", "FROM_YEAR", "TO_YEAR"]
    ).any():
        raise RuntimeError("History output contains duplicate transition keys")
    if audit["CONSTRAINED_RATE"].isna().any():
        raise RuntimeError("One or more production escalation rates are unresolved")
    if ~audit["CONSTRAINED_RATE"].between(
        audit["HARD_LOWER_RATE"], audit["HARD_UPPER_RATE"], inclusive="both"
    ).all():
        raise RuntimeError("One or more production escalation rates exceed hard bounds")

    projected_columns = [f"{metric}_CONSTRAINED_PROJECTED_WAGE" for metric in WAGE_METRICS]
    if projections[projected_columns].isna().any().any():
        raise RuntimeError("One or more constrained projected wages are null")

    ordered_metrics = ["H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90"]
    ordering_violations = pd.Series(False, index=projections.index)
    for lower, upper in zip(ordered_metrics, ordered_metrics[1:]):
        ordering_violations |= projections[
            f"{lower}_CONSTRAINED_PROJECTED_WAGE"
        ].gt(projections[f"{upper}_CONSTRAINED_PROJECTED_WAGE"] + 1e-9)

    return {
        "expected_audit_rows": expected_audit,
        "expected_projection_rows": expected_projections,
        "history_rows": len(history),
        "percentile_ordering_violations": int(ordering_violations.sum()),
    }


def run(
    *,
    input_dir: Path = Path("input_data"),
    base_wages_path: Path = DEFAULT_BASE_WAGES,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    history_start: int = 2021,
    history_end: int = 2025,
    base_year: int = 2025,
    projection_start: int = 2026,
    projection_end: int = 2050,
    shrinkage_k: float = 4.0,
    peer_lower_quantile: float = 0.10,
    peer_upper_quantile: float = 0.90,
    hard_lower_rate: float = -0.02,
    hard_upper_rate: float = 0.08,
    model_version: str = "labor-oews-2021-2025-v1",
) -> dict[str, Path]:
    base_wages_path = Path(base_wages_path)
    if not base_wages_path.is_file():
        raise FileNotFoundError(f"Base wage file not found: {base_wages_path}")
    base_wages = pd.read_parquet(base_wages_path)
    annual_frames = read_annual_oews_files(
        Path(input_dir), list(range(history_start, history_end + 1))
    )
    projections, audit, history = build_labor_escalation_outputs(
        annual_frames,
        base_wages,
        base_year=base_year,
        projection_start=projection_start,
        projection_end=projection_end,
        shrinkage_k=shrinkage_k,
        peer_lower_quantile=peer_lower_quantile,
        peer_upper_quantile=peer_upper_quantile,
        hard_lower_rate=hard_lower_rate,
        hard_upper_rate=hard_upper_rate,
        model_version=model_version,
    )
    validation = validate_outputs(
        projections,
        audit,
        history,
        base_wages,
        projection_start,
        projection_end,
    )

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "projections": output_dir
        / f"labor_wage_projections_{projection_start}_{projection_end}.parquet",
        "rate_audit": output_dir / "labor_escalation_rate_audit.parquet",
        "history": output_dir / "labor_escalation_history.parquet",
    }
    projections.to_parquet(paths["projections"], index=False, compression="zstd")
    audit.to_parquet(paths["rate_audit"], index=False, compression="zstd")
    history.to_parquet(paths["history"], index=False, compression="zstd")

    print(f"Projection rows: {len(projections):,}")
    print(f"Rate audit rows: {len(audit):,}")
    print(f"Historical transition rows: {len(history):,}")
    print("Valid transition counts:")
    print(audit["VALID_TRANSITIONS"].value_counts().sort_index().to_string())
    print("Adjustment reasons:")
    print(audit["ADJUSTMENT_REASONS"].value_counts().head(20).to_string())
    print(
        "Percentile ordering violations: "
        f"{validation['percentile_ordering_violations']:,}"
    )
    for label, path in paths.items():
        print(f"{label}: {path}")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build constrained OEWS labor wage projections from 2021-2025 history."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("input_data"))
    parser.add_argument("--base-wages", type=Path, default=DEFAULT_BASE_WAGES)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--history-start", type=int, default=2021)
    parser.add_argument("--history-end", type=int, default=2025)
    parser.add_argument("--base-year", type=int, default=2025)
    parser.add_argument("--projection-start", type=int, default=2026)
    parser.add_argument("--projection-end", type=int, default=2050)
    parser.add_argument("--shrinkage-k", type=float, default=4.0)
    parser.add_argument("--peer-lower-quantile", type=float, default=0.10)
    parser.add_argument("--peer-upper-quantile", type=float, default=0.90)
    parser.add_argument("--hard-lower-rate", type=float, default=-0.02)
    parser.add_argument("--hard-upper-rate", type=float, default=0.08)
    parser.add_argument("--model-version", default="labor-oews-2021-2025-v1")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(
        input_dir=args.input_dir,
        base_wages_path=args.base_wages,
        output_dir=args.output_dir,
        history_start=args.history_start,
        history_end=args.history_end,
        base_year=args.base_year,
        projection_start=args.projection_start,
        projection_end=args.projection_end,
        shrinkage_k=args.shrinkage_k,
        peer_lower_quantile=args.peer_lower_quantile,
        peer_upper_quantile=args.peer_upper_quantile,
        hard_lower_rate=args.hard_lower_rate,
        hard_upper_rate=args.hard_upper_rate,
        model_version=args.model_version,
    )


if __name__ == "__main__":
    main()