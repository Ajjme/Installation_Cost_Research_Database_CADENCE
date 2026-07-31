"""Process one dated folder of Home Depot roofing HTML captures."""

from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

from src.aggregate_prices import OUTPUT_FILENAMES, aggregate_all
from src.normalize_products import load_geo_seed, normalize, read_jsonl
from src.retailers.home_depot import (
    _LOCAL_FILENAME_RE,
    ScrapeConfig,
    ingest_local_html,
    load_categories,
)

logger = logging.getLogger(__name__)
_BATCH_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DATED_BATCH_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class BatchPaths:
    raw_dir: Path
    normalized_parquet: Path
    normalized_csv: Path
    output_dir: Path


def build_batch_paths(
    batch_name: str,
    raw_root: str = "data_raw/home_depot",
    intermediate_dir: str = "data_intermediate",
    output_root: str = "data_output/home_depot",
) -> BatchPaths:
    """Build material-specific paths that keep collection batches isolated."""
    if not _BATCH_NAME_RE.fullmatch(batch_name):
        raise ValueError(
            "Batch name must contain only letters, numbers, dots, underscores, or hyphens."
        )
    normalized_stem = f"home_depot_products_normalized_{batch_name}"
    return BatchPaths(
        raw_dir=Path(raw_root) / batch_name,
        normalized_parquet=Path(intermediate_dir) / f"{normalized_stem}.parquet",
        normalized_csv=Path(intermediate_dir) / f"{normalized_stem}.csv",
        output_dir=Path(output_root) / batch_name,
    )


def discover_category_keys(local_html_dir: str) -> list[str]:
    """Return category prefixes from standardized local HTML filenames."""
    source_dir = Path(local_html_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Local HTML directory not found: {source_dir}")

    categories = set()
    invalid_names = []
    for html_path in sorted(source_dir.glob("*.html")):
        match = _LOCAL_FILENAME_RE.match(html_path.name)
        if match:
            categories.add(match.group("category"))
        else:
            invalid_names.append(html_path.name)
    if invalid_names:
        raise ValueError(
            "HTML filenames must match '<category>_<zip>_p<page>.html': "
            + ", ".join(invalid_names)
        )
    if not categories:
        raise ValueError(f"No HTML files found in {source_dir}")
    return sorted(categories)


def _ensure_new_batch(paths: BatchPaths) -> None:
    collisions = [
        path
        for path in (
            paths.raw_dir,
            paths.normalized_parquet,
            paths.normalized_csv,
            paths.output_dir,
        )
        if path.exists()
    ]
    if collisions:
        formatted = ", ".join(str(path) for path in collisions)
        raise FileExistsError(
            f"Batch outputs already exist and will not be overwritten: {formatted}"
        )


def process_batch(
    local_html_dir: str,
    *,
    batch_name: Optional[str] = None,
    categories_config: str = "config/home_depot_categories.yml",
    geo_seed: str = "config/geo_seed_zips.csv",
    raw_root: str = "data_raw/home_depot",
    intermediate_dir: str = "data_intermediate",
    output_root: str = "data_output/home_depot",
) -> BatchPaths:
    """Process all material and ZIP captures in one dated folder."""
    source_dir = Path(local_html_dir)
    resolved_batch_name = batch_name or source_dir.name
    if batch_name is None and not _DATED_BATCH_RE.fullmatch(resolved_batch_name):
        raise ValueError(
            "The local HTML directory must use a YYYY-MM-DD batch name, or "
            "--batch-name must be provided explicitly."
        )
    paths = build_batch_paths(
        resolved_batch_name,
        raw_root=raw_root,
        intermediate_dir=intermediate_dir,
        output_root=output_root,
    )
    _ensure_new_batch(paths)

    all_categories = load_categories(categories_config)
    category_keys = discover_category_keys(local_html_dir)
    missing_categories = [key for key in category_keys if key not in all_categories]
    if missing_categories:
        raise ValueError(
            "Categories missing from config: " + ", ".join(missing_categories)
        )
    categories = {key: all_categories[key] for key in category_keys}

    raw_path = ingest_local_html(
        local_html_dir,
        categories,
        ScrapeConfig(out_dir=str(paths.raw_dir), local_html_dir=local_html_dir),
    )
    raw = read_jsonl(str(raw_path))
    if raw.empty:
        raise ValueError(f"No products were extracted from {source_dir}")

    normalized = normalize(raw, load_geo_seed(geo_seed))
    aggregates = aggregate_all(normalized)
    if aggregates["national"].empty:
        raise ValueError(
            "No products have both a material class and source-backed coverage; "
            "normalized raw prices were retained, but spending summaries cannot be built."
        )

    paths.normalized_parquet.parent.mkdir(parents=True, exist_ok=True)
    normalized.to_parquet(paths.normalized_parquet, index=False)
    normalized.to_csv(paths.normalized_csv, index=False)

    paths.output_dir.mkdir(parents=True, exist_ok=False)
    for level, frame in aggregates.items():
        frame.to_csv(paths.output_dir / OUTPUT_FILENAMES[level], index=False)

    logger.info(
        "Processed %d products from %s into %s",
        len(normalized),
        source_dir,
        paths.output_dir,
    )
    return paths


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Process all asphalt, metal, and other configured Home Depot captures "
            "in one dated local HTML folder."
        )
    )
    parser.add_argument("--local-html-dir", required=True)
    parser.add_argument("--batch-name", default=None)
    parser.add_argument("--categories-config", default="config/home_depot_categories.yml")
    parser.add_argument("--geo-seed", default="config/geo_seed_zips.csv")
    parser.add_argument("--raw-root", default="data_raw/home_depot")
    parser.add_argument("--intermediate-dir", default="data_intermediate")
    parser.add_argument("--output-root", default="data_output/home_depot")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    paths = process_batch(
        args.local_html_dir,
        batch_name=args.batch_name,
        categories_config=args.categories_config,
        geo_seed=args.geo_seed,
        raw_root=args.raw_root,
        intermediate_dir=args.intermediate_dir,
        output_root=args.output_root,
    )
    logger.info("Normalized products: %s", paths.normalized_parquet)


if __name__ == "__main__":
    main()