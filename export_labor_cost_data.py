from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from labor_cost_data import (
    WAGE_METRICS,
    build_wage_outputs,
    normalize_code,
    read_oews_files,
)


def build_geography_output(
    shapefile: Path,
    wage_areas: pd.DataFrame,
    shapefile_key: str = "msa7",
    simplify_tolerance: float = 0.01,
) -> tuple[gpd.GeoDataFrame, pd.DataFrame]:
    geographies = gpd.read_file(shapefile)
    if shapefile_key not in geographies.columns:
        raise ValueError(
            f"Shapefile key '{shapefile_key}' was not found. "
            f"Available columns: {', '.join(geographies.columns)}"
        )

    geographies = geographies[[shapefile_key, "geometry"]].rename(
        columns={shapefile_key: "AREA"}
    )
    geographies["AREA"] = normalize_code(geographies["AREA"], width=7)
    geographies = geographies.dissolve(by="AREA", as_index=False)
    if simplify_tolerance > 0:
        geographies["geometry"] = geographies.geometry.simplify(
            simplify_tolerance,
            preserve_topology=True,
        )
    geographies["BOUNDARY_SOURCE_YEAR"] = 2019

    areas = wage_areas[
        ["AREA", "AREA_TITLE", "PRIM_STATE", "GEOGRAPHY_TYPE"]
    ].drop_duplicates()
    wage_codes = set(areas["AREA"])
    geometry_codes = set(geographies["AREA"])

    wage_coverage = areas.copy()
    wage_coverage["COVERAGE_STATUS"] = wage_coverage["AREA"].map(
        lambda area: "matched" if area in geometry_codes else "wage_only"
    )
    geometry_only = pd.DataFrame(
        {
            "AREA": sorted(geometry_codes.difference(wage_codes)),
            "AREA_TITLE": "",
            "PRIM_STATE": "",
            "GEOGRAPHY_TYPE": "msa",
            "COVERAGE_STATUS": "geometry_only",
        }
    )
    coverage = pd.concat([wage_coverage, geometry_only], ignore_index=True)
    coverage = coverage.sort_values(["COVERAGE_STATUS", "AREA"]).reset_index(drop=True)
    return geographies, coverage


def export_labor_cost_data(
    input_dir: Path,
    output_dir: Path,
    shapefile: Path | None,
    shapefile_key: str = "msa7",
    simplify_tolerance: float = 0.01,
    data_year: int = 2025,
) -> dict[str, Path]:
    frames = read_oews_files(input_dir)
    wide, long = build_wage_outputs(frames, data_year=data_year)

    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "wide_parquet": output_dir / "labor_wages_wide.parquet",
        "wide_csv": output_dir / "labor_wages_wide.csv",
        "long_parquet": output_dir / "labor_wages_long.parquet",
    }
    wide.to_parquet(paths["wide_parquet"], index=False, compression="zstd")
    wide.to_csv(paths["wide_csv"], index=False)
    long.to_parquet(paths["long_parquet"], index=False, compression="zstd")

    if shapefile is not None:
        geographies, coverage = build_geography_output(
            shapefile,
            wide,
            shapefile_key,
            simplify_tolerance,
        )
        paths["geographies"] = output_dir / "labor_geographies.parquet"
        paths["geography_coverage"] = output_dir / "labor_geography_coverage.csv"
        geographies.to_parquet(paths["geographies"], index=False, compression="zstd")
        coverage.to_csv(paths["geography_coverage"], index=False)

    expected_long_rows = len(wide) * len(WAGE_METRICS)
    if len(long) != expected_long_rows:
        raise RuntimeError(
            f"Long output has {len(long):,} rows; expected {expected_long_rows:,}"
        )
    if wide.duplicated(["AREA", "OCC_TITLE"]).any():
        raise RuntimeError("Wide output contains duplicate area/occupation keys")
    if long.duplicated(["AREA", "OCC_TITLE", "WAGE_METRIC"]).any():
        raise RuntimeError("Long output contains duplicate area/occupation/metric keys")

    print(f"Wide wage rows: {len(wide):,}")
    print(f"Long wage rows: {len(long):,}")
    print("Resolution counts:")
    print(long["SOURCE_LEVEL"].value_counts(dropna=False).to_string())
    if "geography_coverage" in paths:
        print("Geometry coverage:")
        print(coverage["COVERAGE_STATUS"].value_counts().to_string())
    for label, path in paths.items():
        print(f"{label}: {path}")
    return paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export query-ready OEWS labor wage and geography files."
    )
    parser.add_argument("--input-dir", type=Path, default=Path("input_data"))
    parser.add_argument("--output-dir", type=Path, default=Path("data_output/labor"))
    parser.add_argument(
        "--shapefile",
        type=Path,
        default=Path("geo_shapefiles/OES 2019 Shapefile.shp"),
    )
    parser.add_argument("--shapefile-key", default="msa7")
    parser.add_argument(
        "--simplify-tolerance",
        type=float,
        default=0.01,
        help="Topology-preserving boundary simplification in source CRS units; use 0 to disable.",
    )
    parser.add_argument("--data-year", type=int, default=2025)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_labor_cost_data(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        shapefile=args.shapefile,
        shapefile_key=args.shapefile_key,
        simplify_tolerance=args.simplify_tolerance,
        data_year=args.data_year,
    )


if __name__ == "__main__":
    main()