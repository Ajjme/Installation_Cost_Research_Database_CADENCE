from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pandas as pd


TARGET_OCCUPATIONS_PATH = Path(__file__).resolve().parent / "config" / "target_occupations.csv"
_target_occupations = pd.read_csv(TARGET_OCCUPATIONS_PATH, dtype="string")
TARGET_OCCUPATION_CODES = dict(
    zip(_target_occupations["occupation"], _target_occupations["occupation_code"])
)
TARGET_OCCUPATIONS = list(TARGET_OCCUPATION_CODES)

WAGE_METRICS = [
    "H_PCT10",
    "H_PCT25",
    "H_MEDIAN",
    "H_PCT75",
    "H_PCT90",
    "H_MEAN",
]

REQUIRED_COLUMNS = {"AREA", "AREA_TITLE", "OCC_CODE", "OCC_TITLE", "PRIM_STATE"}
GEOGRAPHY_TYPES = {"msa", "bos", "state", "national"}
DEFAULT_FILES = {
    "msa": "MSA_M2025_dl.xlsx",
    "bos": "BOS_M2025_dl.xlsx",
    "state": "state_M2025_dl.xlsx",
    "national": "national_M2025_dl.xlsx",
}
AREA_TYPE_TO_GEOGRAPHY = {
    1: "national",
    2: "state",
    3: "state",
    4: "msa",
    6: "bos",
}


def read_oews_files(input_dir: Path) -> dict[str, pd.DataFrame]:
    frames = {}
    for geography_type, filename in DEFAULT_FILES.items():
        path = input_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Required OEWS file not found: {path}")
        frames[geography_type] = pd.read_excel(path)
    return frames


def read_consolidated_oews_file(path: Path) -> dict[str, pd.DataFrame]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Required OEWS file not found: {path}")

    frame = pd.read_excel(path)
    frame.columns = frame.columns.str.upper().str.strip()
    required = REQUIRED_COLUMNS | {"AREA_TYPE", "NAICS"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(
            f"Consolidated OEWS data is missing columns: {', '.join(sorted(missing))}"
        )

    frame["NAICS"] = normalize_code(frame["NAICS"], width=6)
    frame["AREA_TYPE"] = pd.to_numeric(frame["AREA_TYPE"], errors="coerce")
    unknown_types = sorted(
        frame.loc[
            frame["NAICS"].eq("000000")
            & ~frame["AREA_TYPE"].isin(AREA_TYPE_TO_GEOGRAPHY),
            "AREA_TYPE",
        ].dropna().unique()
    )
    if unknown_types:
        raise ValueError(f"Unsupported OEWS AREA_TYPE values: {unknown_types}")

    target_codes = set(TARGET_OCCUPATION_CODES.values())
    frame["OCC_CODE"] = normalize_code(frame["OCC_CODE"])
    frame = frame[
        frame["NAICS"].eq("000000") & frame["OCC_CODE"].isin(target_codes)
    ].copy()

    frames = {}
    for geography_type in GEOGRAPHY_TYPES:
        area_types = [
            area_type
            for area_type, mapped_type in AREA_TYPE_TO_GEOGRAPHY.items()
            if mapped_type == geography_type
        ]
        geography_frame = frame[frame["AREA_TYPE"].isin(area_types)].copy()
        duplicate_keys = geography_frame.duplicated(["AREA", "OCC_CODE"], keep=False)
        if duplicate_keys.any():
            examples = (
                geography_frame.loc[duplicate_keys, ["AREA", "OCC_CODE"]]
                .drop_duplicates()
                .head(5)
                .to_dict("records")
            )
            raise ValueError(
                f"Duplicate {geography_type} cross-industry keys found: {examples}"
            )
        frames[geography_type] = geography_frame
    return frames


def read_annual_oews_files(
    input_dir: Path,
    years: range | list[int] = range(2021, 2026),
) -> dict[int, dict[str, pd.DataFrame]]:
    annual_frames = {}
    for year in years:
        path = Path(input_dir) / str(year) / f"all_data_M_{year}.xlsx"
        annual_frames[year] = read_consolidated_oews_file(path)
    return annual_frames


def normalize_code(series: pd.Series, width: int | None = None) -> pd.Series:
    normalized = series.astype("string").str.strip().str.replace(r"\.0$", "", regex=True)
    if width is not None:
        normalized = normalized.str.zfill(width)
    return normalized


def clean_oews_frame(frame: pd.DataFrame, geography_type: str) -> pd.DataFrame:
    if geography_type not in GEOGRAPHY_TYPES:
        raise ValueError(f"Unsupported geography type: {geography_type}")

    cleaned = frame.copy()
    cleaned.columns = cleaned.columns.str.upper().str.strip()
    missing = REQUIRED_COLUMNS.difference(cleaned.columns)
    if missing:
        raise ValueError(
            f"{geography_type} data is missing required columns: {', '.join(sorted(missing))}"
        )

    for metric in WAGE_METRICS:
        if metric not in cleaned.columns:
            cleaned[metric] = pd.NA
        cleaned[metric] = pd.to_numeric(cleaned[metric], errors="coerce")

    cleaned["AREA"] = normalize_code(cleaned["AREA"], width=7)
    cleaned["OCC_CODE"] = normalize_code(cleaned["OCC_CODE"])
    cleaned["PRIM_STATE"] = normalize_code(cleaned["PRIM_STATE"]).str.upper()
    cleaned["GEOGRAPHY_TYPE"] = geography_type
    return cleaned


def _assert_unique(frame: pd.DataFrame, keys: list[str], label: str) -> None:
    duplicates = frame.duplicated(keys, keep=False)
    if duplicates.any():
        examples = frame.loc[duplicates, keys].drop_duplicates().head(5).to_dict("records")
        raise ValueError(f"Duplicate {label} keys found: {examples}")


def build_wage_outputs(
    frames: Mapping[str, pd.DataFrame],
    data_year: int = 2025,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    missing_frames = GEOGRAPHY_TYPES.difference(frames)
    if missing_frames:
        raise ValueError(f"Missing OEWS frames: {', '.join(sorted(missing_frames))}")

    cleaned = {
        geography_type: clean_oews_frame(frames[geography_type], geography_type)
        for geography_type in GEOGRAPHY_TYPES
    }
    local = pd.concat([cleaned["msa"], cleaned["bos"]], ignore_index=True)

    area_columns = ["AREA", "AREA_TITLE", "PRIM_STATE", "GEOGRAPHY_TYPE"]
    areas = local[area_columns].drop_duplicates()
    _assert_unique(areas, ["AREA"], "local area")

    targets = pd.DataFrame(
        [
            {"OCC_CODE": code, "OCC_TITLE": title}
            for title, code in TARGET_OCCUPATION_CODES.items()
        ]
    )
    national_codes = set(cleaned["national"]["OCC_CODE"])
    missing_codes = set(TARGET_OCCUPATION_CODES.values()).difference(national_codes)
    if missing_codes:
        raise ValueError(
            "National data is missing target occupation codes: "
            + ", ".join(sorted(missing_codes))
        )

    grid = areas.merge(targets, how="cross")
    target_codes = set(TARGET_OCCUPATION_CODES.values())
    local_targets = local[local["OCC_CODE"].isin(target_codes)].copy()
    _assert_unique(local_targets, ["AREA", "OCC_CODE"], "local wage")

    state_targets = cleaned["state"][
        cleaned["state"]["OCC_CODE"].isin(target_codes)
    ].copy()
    _assert_unique(state_targets, ["PRIM_STATE", "OCC_CODE"], "state wage")

    national_wages = cleaned["national"][
        cleaned["national"]["OCC_CODE"].isin(target_codes)
    ].copy()
    _assert_unique(national_wages, ["OCC_CODE"], "national wage")

    wide = grid.merge(
        local_targets[["AREA", "OCC_CODE", *WAGE_METRICS]],
        on=["AREA", "OCC_CODE"],
        how="left",
    )
    state_columns = {
        metric: f"{metric}__STATE" for metric in WAGE_METRICS
    }
    wide = wide.merge(
        state_targets[["AREA", "AREA_TITLE", "PRIM_STATE", "OCC_CODE", *WAGE_METRICS]].rename(
            columns={
                "AREA": "STATE_SOURCE_AREA",
                "AREA_TITLE": "STATE_SOURCE_TITLE",
                **state_columns,
            }
        ),
        on=["PRIM_STATE", "OCC_CODE"],
        how="left",
    )
    national_columns = {
        metric: f"{metric}__NATIONAL" for metric in WAGE_METRICS
    }
    wide = wide.merge(
        national_wages[["AREA", "AREA_TITLE", "OCC_CODE", *WAGE_METRICS]].rename(
            columns={
                "AREA": "NATIONAL_SOURCE_AREA",
                "AREA_TITLE": "NATIONAL_SOURCE_TITLE",
                **national_columns,
            }
        ),
        on="OCC_CODE",
        how="left",
    )

    long_parts = []
    for metric in WAGE_METRICS:
        local_value = wide[metric]
        state_value = wide[f"{metric}__STATE"]
        national_value = wide[f"{metric}__NATIONAL"]
        resolved = local_value.where(local_value.notna(), state_value)
        resolved = resolved.where(resolved.notna(), national_value)

        source_level = pd.Series("unresolved", index=wide.index, dtype="string")
        source_level.loc[national_value.notna()] = "national"
        source_level.loc[state_value.notna()] = "state"
        source_level.loc[local_value.notna()] = "local"

        source_area = pd.Series(pd.NA, index=wide.index, dtype="string")
        source_area.loc[national_value.notna()] = wide.loc[
            national_value.notna(), "NATIONAL_SOURCE_AREA"
        ]
        source_area.loc[state_value.notna()] = wide.loc[state_value.notna(), "STATE_SOURCE_AREA"]
        source_area.loc[local_value.notna()] = wide.loc[local_value.notna(), "AREA"]

        wide[metric] = resolved
        wide[f"{metric}_SOURCE_LEVEL"] = source_level
        wide[f"{metric}_SOURCE_AREA"] = source_area

        metric_rows = wide[
            ["AREA", "AREA_TITLE", "PRIM_STATE", "GEOGRAPHY_TYPE", "OCC_CODE", "OCC_TITLE"]
        ].copy()
        metric_rows["WAGE_METRIC"] = metric
        metric_rows["HOURLY_WAGE"] = resolved
        metric_rows["SOURCE_LEVEL"] = source_level
        metric_rows["SOURCE_AREA"] = source_area
        metric_rows["IS_IMPUTED"] = source_level.isin(["state", "national"])
        long_parts.append(metric_rows)

    wide["DATA_YEAR"] = data_year
    long = pd.concat(long_parts, ignore_index=True)
    long["DATA_YEAR"] = data_year

    wide_columns = [
        "DATA_YEAR",
        "GEOGRAPHY_TYPE",
        "AREA",
        "AREA_TITLE",
        "PRIM_STATE",
        "OCC_CODE",
        "OCC_TITLE",
    ]
    for metric in WAGE_METRICS:
        wide_columns.extend(
            [metric, f"{metric}_SOURCE_LEVEL", f"{metric}_SOURCE_AREA"]
        )
    wide = wide[wide_columns].sort_values(["AREA", "OCC_TITLE"]).reset_index(drop=True)
    long = long.sort_values(["AREA", "OCC_TITLE", "WAGE_METRIC"]).reset_index(drop=True)
    return wide, long