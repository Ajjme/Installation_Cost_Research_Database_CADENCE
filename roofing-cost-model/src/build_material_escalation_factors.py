"""Build material escalation factors from normalized FRED/BLS index data.

Reads ``data_intermediate/fred_indexes.parquet`` (produced by
``fetch_fred_indexes.py``), expands each series onto the material classes it
maps to, and computes a per-month escalation factor relative to a chosen base
month:

    escalation_factor = index_value / base_index_value

These factors are NATIONAL time-adjustment multipliers. They contain no
geography; local price levels must come from retailer scraping.

Usage:
    python -m src.build_material_escalation_factors --base-month 2024-01

Output (``data_output/material_escalation_factors.parquet``) columns:
    material_class, series_id, series_name, date, month, index_value,
    base_month, base_index_value, escalation_factor, priority, source
"""

from __future__ import annotations

import argparse
import logging
import re
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

DEFAULT_INPUT_PATH = _PROJECT_ROOT / "data_intermediate" / "fred_indexes.parquet"
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "data_output" / "material_escalation_factors.parquet"

OUTPUT_COLUMNS = [
    "material_class",
    "series_id",
    "series_name",
    "date",
    "month",
    "index_value",
    "base_month",
    "base_index_value",
    "escalation_factor",
    "priority",
    "source",
]

_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class EscalationError(ValueError):
    """Raised for invalid inputs while building escalation factors."""


def _validate_base_month(base_month: str) -> str:
    """Validate the base-month string is in ``YYYY-MM`` form."""
    if not isinstance(base_month, str) or not _MONTH_RE.match(base_month):
        raise EscalationError(
            f"base_month must be formatted 'YYYY-MM' (e.g. '2024-01'); got {base_month!r}."
        )
    return base_month


def _explode_material_mapping(df: pd.DataFrame) -> pd.DataFrame:
    """Expand the list-valued ``material_mapping`` column into ``material_class``.

    One FRED series can map to several material classes, so this produces one
    row per (series observation, material_class) pair.
    """
    work = df.copy()
    # Tolerate mappings stored as lists, numpy arrays, or comma strings.
    def _to_list(val: object) -> list[str]:
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        if val is None:
            return []
        try:
            return [str(v) for v in list(val)]
        except TypeError:
            return []

    work["material_class"] = work["material_mapping"].map(_to_list)
    work = work.explode("material_class", ignore_index=True)
    work = work.dropna(subset=["material_class"])
    work = work[work["material_class"].astype(str).str.len() > 0]
    return work


def compute_escalation_factors(df: pd.DataFrame, base_month: str) -> pd.DataFrame:
    """Compute escalation factors for every material class and series.

    Args:
        df: Normalized index frame (schema from ``fetch_fred_indexes``).
        base_month: Reference month as ``YYYY-MM``.

    Returns:
        Frame following ``OUTPUT_COLUMNS``.

    Raises:
        EscalationError: If ``base_month`` is malformed or absent from the data.
    """
    _validate_base_month(base_month)

    if df is None or df.empty:
        raise EscalationError("Input index frame is empty; nothing to escalate.")

    work = df.copy()
    # Clean: parse dates and coerce values, dropping nonnumeric/missing rows.
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work["index_value"] = pd.to_numeric(work["value"], errors="coerce")
    work = work.dropna(subset=["date", "index_value"])
    if work.empty:
        raise EscalationError("No numeric observations remain after cleaning.")

    work["month"] = work["date"].dt.to_period("M").astype(str)

    if base_month not in set(work["month"]):
        available = ", ".join(sorted(set(work["month"]))[:6])
        raise EscalationError(
            f"Base month '{base_month}' not present in the data. "
            f"Earliest available months include: {available} ..."
        )

    work = _explode_material_mapping(work)

    # Base index value per series (the multiplier denominator).
    base_rows = work[work["month"] == base_month][["series_id", "index_value"]]
    base_index = (
        base_rows.groupby("series_id")["index_value"].mean().rename("base_index_value")
    )

    merged = work.merge(base_index, on="series_id", how="left")

    # A series with no observation in the base month cannot be escalated; drop
    # it with a warning rather than emitting NaN factors.
    missing_base = merged["base_index_value"].isna()
    if missing_base.any():
        dropped = sorted(merged.loc[missing_base, "series_id"].unique())
        logger.warning(
            "Dropping %d series with no base-month (%s) observation: %s",
            len(dropped),
            base_month,
            ", ".join(dropped),
        )
        merged = merged[~missing_base]

    if merged.empty:
        raise EscalationError(
            f"No series had an observation in base month '{base_month}'."
        )

    merged["base_month"] = base_month
    merged["escalation_factor"] = merged["index_value"] / merged["base_index_value"]

    out = merged[OUTPUT_COLUMNS].copy()
    out = out.sort_values(["material_class", "series_id", "date"]).reset_index(drop=True)
    return out


def load_indexes(input_path: Path = DEFAULT_INPUT_PATH) -> pd.DataFrame:
    """Load the normalized FRED index Parquet."""
    path = Path(input_path)
    if not path.is_file():
        raise EscalationError(
            f"Index file not found: {path}. Run fetch_fred_indexes first."
        )
    return pd.read_parquet(path)


def write_factors(df: pd.DataFrame, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    """Write the escalation-factor frame to Parquet (idempotent overwrite)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    logger.info("Wrote escalation factors: %s (%d rows)", output_path, len(df))


def run(
    base_month: str,
    *,
    input_path: Path = DEFAULT_INPUT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
) -> pd.DataFrame:
    """Load indexes, compute factors for ``base_month``, and write output."""
    df = load_indexes(input_path)
    factors = compute_escalation_factors(df, base_month)
    write_factors(factors, output_path)
    return factors


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Build material escalation factors from FRED/BLS indexes."
    )
    parser.add_argument(
        "--base-month",
        required=True,
        help="Reference month as YYYY-MM (e.g. 2024-01). Factor = 1.0 here.",
    )
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT_PATH),
        help="Normalized FRED indexes Parquet.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Escalation-factor Parquet output path.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    factors = run(
        args.base_month,
        input_path=Path(args.input),
        output_path=Path(args.output),
    )
    logger.info(
        "Done. %d rows across %d material classes.",
        len(factors),
        factors["material_class"].nunique() if not factors.empty else 0,
    )


if __name__ == "__main__":
    main()
