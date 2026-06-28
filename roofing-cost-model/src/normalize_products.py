"""Normalize raw Home Depot product rows into a clean, analysis-ready table.

Responsibilities:
  * Parse price strings into floats.
  * Parse coverage (square feet per unit) from free text.
  * Derive price-per-square-foot and price-per-square (1 square = 100 sq ft).
  * Derive bulk price-per-square-foot/square and bulk discount percentage.
  * Assign material_class via src.classify_materials.
  * Join geography (city/state/CBSA) from the ZIP seed file.
  * Flag products with missing or suspicious coverage.

Usage:
    python -m src.normalize_products \
        --input data_raw/home_depot/YYYY-MM-DD/home_depot_products_raw.jsonl \
        --output data_intermediate/home_depot_products_normalized.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from src.classify_materials import classify_product

logger = logging.getLogger(__name__)

# One roofing "square" equals 100 square feet.
SQFT_PER_SQUARE = 100.0

# Coverage outside this range (sq ft per unit) is treated as suspicious. A
# single bundle of shingles typically covers ~33 sq ft; a pallet/square product
# may cover ~100. Values far outside are likely parsing errors.
COVERAGE_MIN_SQFT = 5.0
COVERAGE_MAX_SQFT = 2000.0

# Columns required in the normalized output, in order.
OUTPUT_COLUMNS = [
    "retailer",
    "scrape_date",
    "zip_code",
    "city",
    "state",
    "cbsa_name",
    "cbsa_code",
    "store_id",
    "category_key",
    "product_id",
    "sku",
    "model_number",
    "product_name",
    "brand",
    "product_url",
    "material_class",
    "retail_price_per_unit",
    "bulk_price_per_unit",
    "bulk_threshold",
    "coverage_sqft_per_unit",
    "price_per_sqft",
    "price_per_square",
    "bulk_price_per_sqft",
    "bulk_price_per_square",
    "bulk_discount_pct",
    "availability_status",
    "source_method",
    "coverage_flag",
]

# Commas are stripped before matching, so a plain number pattern is sufficient.
_PRICE_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")
_COVERAGE_PATTERNS = (
    re.compile(r"(\d+(?:\.\d+)?)\s*sq\.?\s*ft\.?\s*per\s*bundle", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*sq\.?\s*ft\.?\s*/\s*bundle", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*square\s*f(?:ee|oo)?t", re.IGNORECASE),
    re.compile(r"(\d+(?:\.\d+)?)\s*sq\.?\s*ft", re.IGNORECASE),
)


def parse_price(value: Any) -> Optional[float]:
    """Parse a price from a number or a string like '$41.47' or '1,234.50'.

    Returns None when no numeric value can be found.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _PRICE_RE.search(str(value).replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_coverage_sqft(value: Any) -> Optional[float]:
    """Parse coverage in square feet from text like '33.33 sq. ft. per bundle'.

    Returns None when no coverage can be found. Does not impute.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value)
    for pattern in _COVERAGE_PATTERNS:
        match = pattern.search(text)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def price_per_sqft(price_per_unit: Optional[float], coverage_sqft: Optional[float]) -> Optional[float]:
    """Dollars per square foot, or None if inputs are missing/invalid."""
    if not price_per_unit or not coverage_sqft:
        return None
    if coverage_sqft <= 0:
        return None
    return price_per_unit / coverage_sqft


def price_per_square(price_per_unit: Optional[float], coverage_sqft: Optional[float]) -> Optional[float]:
    """Dollars per roofing square (100 sq ft), or None."""
    pps = price_per_sqft(price_per_unit, coverage_sqft)
    if pps is None:
        return None
    return pps * SQFT_PER_SQUARE


def bulk_discount_pct(
    retail_price: Optional[float], bulk_price: Optional[float]
) -> Optional[float]:
    """Fractional discount = 1 - bulk/retail, or None when not computable."""
    if not retail_price or not bulk_price:
        return None
    if retail_price <= 0:
        return None
    return 1.0 - (bulk_price / retail_price)


def _coverage_flag(coverage: Optional[float]) -> str:
    if coverage is None:
        return "missing"
    if coverage < COVERAGE_MIN_SQFT or coverage > COVERAGE_MAX_SQFT:
        return "suspicious"
    return "ok"


def load_geo_seed(path: Optional[str]) -> pd.DataFrame:
    """Load the ZIP -> city/state/CBSA seed table. Returns empty frame if missing."""
    cols = ["zip_code", "city", "state", "cbsa_name", "cbsa_code"]
    if not path or not Path(path).exists():
        logger.warning("Geo seed file not found at %s; geography will be blank.", path)
        return pd.DataFrame(columns=cols)
    geo = pd.read_csv(path, dtype={"zip_code": str, "cbsa_code": str})
    for col in cols:
        if col not in geo.columns:
            geo[col] = pd.NA
    return geo[cols].copy()


def read_jsonl(path: str) -> pd.DataFrame:
    """Read a JSONL file of raw product rows into a DataFrame."""
    rows = []
    with open(path, "r", encoding="utf-8-sig") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSONL line %d: %s", line_no, exc)
    return pd.DataFrame(rows)


def normalize(df: pd.DataFrame, geo: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Normalize raw product rows. Returns a frame with OUTPUT_COLUMNS.

    Robust to missing columns and malformed individual rows.
    """
    if df is None or df.empty:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    df = df.copy()

    # Ensure all referenced source columns exist.
    for col in [
        "retailer", "scrape_date", "zip_code", "store_id", "category_key",
        "product_id", "sku", "model_number", "product_name", "brand",
        "product_url", "retail_price_per_unit", "bulk_price_per_unit",
        "bulk_threshold", "coverage_sqft_per_unit", "unit_text",
        "price_per_sqft_raw", "availability_status", "source_method",
        "material", "product_type", "breadcrumb",
    ]:
        if col not in df.columns:
            df[col] = pd.NA

    df["zip_code"] = df["zip_code"].astype("string")

    # Parse prices.
    df["retail_price_per_unit"] = df["retail_price_per_unit"].map(parse_price)
    df["bulk_price_per_unit"] = df["bulk_price_per_unit"].map(parse_price)

    # Coverage: prefer the explicit field, else parse from unit_text/product_name.
    def _resolve_coverage(row: pd.Series) -> Optional[float]:
        explicit = parse_coverage_sqft(row.get("coverage_sqft_per_unit"))
        if explicit is not None:
            return explicit
        for fallback_field in ("unit_text", "price_per_sqft_raw", "product_name"):
            parsed = parse_coverage_sqft(row.get(fallback_field))
            if parsed is not None:
                return parsed
        return None

    df["coverage_sqft_per_unit"] = df.apply(_resolve_coverage, axis=1)

    # Derived price metrics.
    df["price_per_sqft"] = df.apply(
        lambda r: price_per_sqft(r["retail_price_per_unit"], r["coverage_sqft_per_unit"]),
        axis=1,
    )
    df["price_per_square"] = df.apply(
        lambda r: price_per_square(r["retail_price_per_unit"], r["coverage_sqft_per_unit"]),
        axis=1,
    )
    df["bulk_price_per_sqft"] = df.apply(
        lambda r: price_per_sqft(r["bulk_price_per_unit"], r["coverage_sqft_per_unit"]),
        axis=1,
    )
    df["bulk_price_per_square"] = df.apply(
        lambda r: price_per_square(r["bulk_price_per_unit"], r["coverage_sqft_per_unit"]),
        axis=1,
    )
    df["bulk_discount_pct"] = df.apply(
        lambda r: bulk_discount_pct(r["retail_price_per_unit"], r["bulk_price_per_unit"]),
        axis=1,
    )

    df["coverage_flag"] = df["coverage_sqft_per_unit"].map(_coverage_flag)

    # Classify materials.
    df["material_class"] = df.apply(lambda r: classify_product(r.to_dict()), axis=1)

    # Join geography.
    if geo is None:
        geo = pd.DataFrame(columns=["zip_code", "city", "state", "cbsa_name", "cbsa_code"])
    geo = geo.copy()
    geo["zip_code"] = geo["zip_code"].astype("string")
    df = df.merge(geo, on="zip_code", how="left", suffixes=("", "_geo"))

    for col in ("city", "state", "cbsa_name", "cbsa_code"):
        if col not in df.columns:
            df[col] = pd.NA

    # Ensure all output columns exist, then select/order them.
    for col in OUTPUT_COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA

    return df[OUTPUT_COLUMNS].copy()


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Normalize raw Home Depot product rows.")
    parser.add_argument("--input", required=True, help="Path to raw products JSONL file.")
    parser.add_argument(
        "--output",
        default="data_intermediate/home_depot_products_normalized.parquet",
        help="Output Parquet path. A sibling .csv is also written.",
    )
    parser.add_argument(
        "--geo-seed",
        default="config/geo_seed_zips.csv",
        help="ZIP -> city/state/CBSA seed CSV.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    raw = read_jsonl(args.input)
    logger.info("Read %d raw product rows from %s", len(raw), args.input)

    geo = load_geo_seed(args.geo_seed)
    normalized = normalize(raw, geo)
    logger.info("Normalized %d rows", len(normalized))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        normalized.to_parquet(out_path, index=False)
        logger.info("Wrote Parquet: %s", out_path)
    except Exception as exc:  # pragma: no cover - depends on optional engine
        logger.error("Failed to write Parquet (%s); CSV will still be written.", exc)

    csv_path = out_path.with_suffix(".csv")
    normalized.to_csv(csv_path, index=False)
    logger.info("Wrote CSV: %s", csv_path)


if __name__ == "__main__":
    main()
