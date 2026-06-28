"""Aggregate normalized product prices to ZIP, state, CBSA, and national levels.

For each geography level we group by scrape_date, retailer, the geography key,
and material_class, then compute price-per-square distribution statistics plus
bulk metrics.

Usage:
    python -m src.aggregate_prices \
        --input data_intermediate/home_depot_products_normalized.parquet \
        --out-dir data_output
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

# Geography level -> the grouping key columns that identify that geography.
GEO_LEVELS = {
    "zip": ["zip_code"],
    "state": ["state"],
    "cbsa": ["cbsa_code", "cbsa_name"],
    "national": [],
}

OUTPUT_FILENAMES = {
    "zip": "home_depot_material_price_by_zip.csv",
    "state": "home_depot_material_price_by_state.csv",
    "cbsa": "home_depot_material_price_by_cbsa.csv",
    "national": "home_depot_material_price_national.csv",
}

_BASE_KEYS = ["scrape_date", "retailer", "material_class"]


def _p25(series: pd.Series) -> float:
    return series.quantile(0.25)


def _p75(series: pd.Series) -> float:
    return series.quantile(0.75)


def aggregate_level(df: pd.DataFrame, level: str) -> pd.DataFrame:
    """Aggregate the normalized frame to a single geography level."""
    if level not in GEO_LEVELS:
        raise ValueError(f"Unknown geography level: {level}")

    geo_keys = GEO_LEVELS[level]
    group_keys = _BASE_KEYS + geo_keys

    if df is None or df.empty:
        return pd.DataFrame(
            columns=group_keys
            + [
                "median_price_per_square",
                "p25_price_per_square",
                "p75_price_per_square",
                "min_price_per_square",
                "max_price_per_square",
                "product_count",
                "store_count",
                "median_bulk_price_per_square",
                "median_bulk_discount_pct",
            ]
        )

    usable = df[df["price_per_square"].notna() & df["material_class"].notna()].copy()
    if usable.empty:
        return pd.DataFrame(columns=group_keys)

    grouped = (
        usable.groupby(group_keys, dropna=False)
        .agg(
            median_price_per_square=("price_per_square", "median"),
            p25_price_per_square=("price_per_square", _p25),
            p75_price_per_square=("price_per_square", _p75),
            min_price_per_square=("price_per_square", "min"),
            max_price_per_square=("price_per_square", "max"),
            product_count=("product_id", "nunique"),
            store_count=("store_id", "nunique"),
            median_bulk_price_per_square=("bulk_price_per_square", "median"),
            median_bulk_discount_pct=("bulk_discount_pct", "median"),
        )
        .reset_index()
    )
    return grouped


def aggregate_all(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Return a dict of {level: aggregated frame} for all geography levels."""
    return {level: aggregate_level(df, level) for level in GEO_LEVELS}


def load_input(path: str) -> pd.DataFrame:
    """Load a normalized Parquet or CSV input."""
    p = Path(path)
    if p.suffix.lower() == ".csv":
        return pd.read_csv(p, dtype={"zip_code": str, "cbsa_code": str})
    return pd.read_parquet(p)


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Aggregate normalized roofing prices.")
    parser.add_argument(
        "--input",
        default="data_intermediate/home_depot_products_normalized.parquet",
        help="Normalized products Parquet or CSV.",
    )
    parser.add_argument("--out-dir", default="data_output", help="Output directory.")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    df = load_input(args.input)
    logger.info("Loaded %d normalized rows from %s", len(df), args.input)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for level, frame in aggregate_all(df).items():
        out_path = out_dir / OUTPUT_FILENAMES[level]
        frame.to_csv(out_path, index=False)
        logger.info("Wrote %s (%d rows)", out_path, len(frame))


if __name__ == "__main__":
    main()
