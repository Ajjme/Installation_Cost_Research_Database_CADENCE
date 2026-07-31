from __future__ import annotations

import math
from collections.abc import Mapping

import pandas as pd

from labor_cost_data import (
    TARGET_OCCUPATION_CODES,
    WAGE_METRICS,
    clean_oews_frame,
)


RATE_KEYS = ["AREA", "OCC_CODE", "WAGE_METRIC"]


def _target_frame(frame: pd.DataFrame, geography_type: str) -> pd.DataFrame:
    cleaned = clean_oews_frame(frame, geography_type)
    return cleaned[
        cleaned["OCC_CODE"].isin(TARGET_OCCUPATION_CODES.values())
    ].copy()


def build_historical_panel(
    annual_frames: Mapping[int, Mapping[str, pd.DataFrame]],
    base_wages: pd.DataFrame,
) -> pd.DataFrame:
    grid_columns = [
        "AREA",
        "AREA_TITLE",
        "PRIM_STATE",
        "GEOGRAPHY_TYPE",
        "OCC_CODE",
        "OCC_TITLE",
    ]
    grid = base_wages[grid_columns].drop_duplicates().copy()
    parts = []

    for year in sorted(annual_frames):
        frames = annual_frames[year]
        local = pd.concat(
            [_target_frame(frames["msa"], "msa"), _target_frame(frames["bos"], "bos")],
            ignore_index=True,
        )
        state = _target_frame(frames["state"], "state")
        national = _target_frame(frames["national"], "national")

        year_grid = grid.merge(
            local[["AREA", "OCC_CODE", *WAGE_METRICS]],
            on=["AREA", "OCC_CODE"],
            how="left",
        )
        year_grid = year_grid.merge(
            state[["AREA", "PRIM_STATE", "OCC_CODE", *WAGE_METRICS]].rename(
                columns={
                    "AREA": "STATE_SOURCE_AREA",
                    **{metric: f"{metric}__STATE" for metric in WAGE_METRICS},
                }
            ),
            on=["PRIM_STATE", "OCC_CODE"],
            how="left",
        )
        year_grid = year_grid.merge(
            national[["AREA", "OCC_CODE", *WAGE_METRICS]].rename(
                columns={
                    "AREA": "NATIONAL_SOURCE_AREA",
                    **{metric: f"{metric}__NATIONAL" for metric in WAGE_METRICS},
                }
            ),
            on="OCC_CODE",
            how="left",
        )

        for metric in WAGE_METRICS:
            local_value = year_grid[metric]
            state_value = year_grid[f"{metric}__STATE"]
            national_value = year_grid[f"{metric}__NATIONAL"]
            resolved = local_value.where(local_value.notna(), state_value)
            resolved = resolved.where(resolved.notna(), national_value)

            source_level = pd.Series("unresolved", index=year_grid.index, dtype="string")
            source_level.loc[national_value.notna()] = "national"
            source_level.loc[state_value.notna()] = "state"
            source_level.loc[local_value.notna()] = "local"
            source_area = pd.Series(pd.NA, index=year_grid.index, dtype="string")
            source_area.loc[national_value.notna()] = year_grid.loc[
                national_value.notna(), "NATIONAL_SOURCE_AREA"
            ]
            source_area.loc[state_value.notna()] = year_grid.loc[
                state_value.notna(), "STATE_SOURCE_AREA"
            ]
            source_area.loc[local_value.notna()] = year_grid.loc[local_value.notna(), "AREA"]

            metric_rows = year_grid[grid_columns].copy()
            metric_rows["DATA_YEAR"] = year
            metric_rows["WAGE_METRIC"] = metric
            metric_rows["HOURLY_WAGE"] = resolved
            metric_rows["SOURCE_LEVEL"] = source_level
            metric_rows["SOURCE_AREA"] = source_area
            parts.append(metric_rows)

    return pd.concat(parts, ignore_index=True).sort_values(
        [*RATE_KEYS, "DATA_YEAR"]
    ).reset_index(drop=True)


def build_transition_audit(panel: pd.DataFrame) -> pd.DataFrame:
    ordered = panel.sort_values([*RATE_KEYS, "DATA_YEAR"]).copy()
    grouped = ordered.groupby(RATE_KEYS, sort=False)
    ordered["FROM_YEAR"] = grouped["DATA_YEAR"].shift()
    ordered["FROM_WAGE"] = grouped["HOURLY_WAGE"].shift()
    ordered["FROM_SOURCE_LEVEL"] = grouped["SOURCE_LEVEL"].shift()
    ordered["FROM_SOURCE_AREA"] = grouped["SOURCE_AREA"].shift()
    ordered = ordered[ordered["FROM_YEAR"].notna()].copy()
    ordered["FROM_YEAR"] = ordered["FROM_YEAR"].astype(int)
    ordered = ordered.rename(
        columns={
            "DATA_YEAR": "TO_YEAR",
            "HOURLY_WAGE": "TO_WAGE",
            "SOURCE_LEVEL": "TO_SOURCE_LEVEL",
            "SOURCE_AREA": "TO_SOURCE_AREA",
        }
    )

    consecutive = ordered["TO_YEAR"].sub(ordered["FROM_YEAR"]).eq(1)
    positive = ordered["FROM_WAGE"].gt(0) & ordered["TO_WAGE"].gt(0)
    same_source = (
        ordered["FROM_SOURCE_LEVEL"].eq(ordered["TO_SOURCE_LEVEL"])
        & ordered["FROM_SOURCE_AREA"].eq(ordered["TO_SOURCE_AREA"])
    )
    ordered["EXCLUSION_REASON"] = ""
    ordered.loc[~consecutive, "EXCLUSION_REASON"] = "nonconsecutive_years"
    ordered.loc[consecutive & ~positive, "EXCLUSION_REASON"] = "missing_or_invalid_wage"
    ordered.loc[consecutive & positive & ~same_source, "EXCLUSION_REASON"] = "source_changed"
    ordered["IS_INCLUDED"] = ordered["EXCLUSION_REASON"].eq("")
    ordered["LOG_CHANGE"] = pd.NA
    included = ordered["IS_INCLUDED"]
    ordered.loc[included, "LOG_CHANGE"] = (
        ordered.loc[included, "TO_WAGE"] / ordered.loc[included, "FROM_WAGE"]
    ).map(math.log)
    ordered["ANNUAL_CHANGE"] = pd.NA
    ordered.loc[included, "ANNUAL_CHANGE"] = ordered.loc[included, "LOG_CHANGE"].map(
        math.expm1
    )
    return ordered.reset_index(drop=True)


def summarize_raw_rates(
    transitions: pd.DataFrame,
    keys: list[str] | None = None,
) -> pd.DataFrame:
    keys = keys or RATE_KEYS
    grouped = transitions.groupby(keys, dropna=False, sort=True)
    summary = grouped.agg(
        TRANSITIONS_TOTAL=("IS_INCLUDED", "size"),
        VALID_TRANSITIONS=("IS_INCLUDED", "sum"),
        EXCLUDED_TRANSITIONS=("IS_INCLUDED", lambda values: int((~values).sum())),
    ).reset_index()
    included = transitions[transitions["IS_INCLUDED"]].copy()
    rates = included.groupby(keys, dropna=False)["LOG_CHANGE"].agg(["mean", "std"]).reset_index()
    rates["RAW_RATE"] = rates["mean"].map(math.expm1)
    rates = rates.rename(columns={"std": "LOG_CHANGE_STD_DEV"}).drop(columns="mean")
    excluded = transitions[~transitions["IS_INCLUDED"]]
    exclusion_reasons = (
        excluded.groupby(keys, dropna=False)["EXCLUSION_REASON"]
        .agg(lambda values: "|".join(sorted(set(values))))
        .rename("EXCLUSION_REASONS")
        .reset_index()
    )
    return summary.merge(rates, on=keys, how="left").merge(
        exclusion_reasons, on=keys, how="left"
    )


def build_benchmark_rates(
    annual_frames: Mapping[int, Mapping[str, pd.DataFrame]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    state_parts = []
    national_parts = []
    for year in sorted(annual_frames):
        state = _target_frame(annual_frames[year]["state"], "state")
        national = _target_frame(annual_frames[year]["national"], "national")
        for frame, destination, key_column in [
            (state, state_parts, "PRIM_STATE"),
            (national, national_parts, None),
        ]:
            id_columns = ["OCC_CODE"] + ([key_column] if key_column else [])
            melted = frame[id_columns + WAGE_METRICS].melt(
                id_vars=id_columns,
                value_vars=WAGE_METRICS,
                var_name="WAGE_METRIC",
                value_name="HOURLY_WAGE",
            )
            melted["DATA_YEAR"] = year
            destination.append(melted)

    state_panel = pd.concat(state_parts, ignore_index=True)
    national_panel = pd.concat(national_parts, ignore_index=True)
    state_transitions = _direct_transitions(
        state_panel, ["PRIM_STATE", "OCC_CODE", "WAGE_METRIC"]
    )
    national_transitions = _direct_transitions(
        national_panel, ["OCC_CODE", "WAGE_METRIC"]
    )
    return (
        summarize_raw_rates(
            state_transitions, ["PRIM_STATE", "OCC_CODE", "WAGE_METRIC"]
        ),
        summarize_raw_rates(national_transitions, ["OCC_CODE", "WAGE_METRIC"]),
    )


def _direct_transitions(panel: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    ordered = panel.sort_values([*keys, "DATA_YEAR"]).copy()
    grouped = ordered.groupby(keys, sort=False)
    ordered["FROM_YEAR"] = grouped["DATA_YEAR"].shift()
    ordered["FROM_WAGE"] = grouped["HOURLY_WAGE"].shift()
    ordered = ordered[ordered["FROM_YEAR"].notna()].copy()
    ordered["TO_YEAR"] = ordered["DATA_YEAR"]
    valid = (
        ordered["TO_YEAR"].sub(ordered["FROM_YEAR"]).eq(1)
        & ordered["FROM_WAGE"].gt(0)
        & ordered["HOURLY_WAGE"].gt(0)
    )
    ordered["IS_INCLUDED"] = valid
    ordered["EXCLUSION_REASON"] = ""
    nonconsecutive = ~ordered["TO_YEAR"].sub(ordered["FROM_YEAR"]).eq(1)
    ordered.loc[nonconsecutive, "EXCLUSION_REASON"] = "nonconsecutive_years"
    ordered.loc[~nonconsecutive & ~valid, "EXCLUSION_REASON"] = "missing_or_invalid_wage"
    ordered["LOG_CHANGE"] = pd.NA
    ordered.loc[valid, "LOG_CHANGE"] = (
        ordered.loc[valid, "HOURLY_WAGE"] / ordered.loc[valid, "FROM_WAGE"]
    ).map(math.log)
    return ordered


def constrain_rates(
    raw_rates: pd.DataFrame,
    state_rates: pd.DataFrame,
    national_rates: pd.DataFrame,
    *,
    shrinkage_k: float = 4.0,
    peer_lower_quantile: float = 0.10,
    peer_upper_quantile: float = 0.90,
    hard_lower_rate: float = -0.02,
    hard_upper_rate: float = 0.08,
) -> pd.DataFrame:
    if shrinkage_k <= 0:
        raise ValueError("shrinkage_k must be greater than zero")
    if not 0 <= peer_lower_quantile < peer_upper_quantile <= 1:
        raise ValueError("peer quantiles must satisfy 0 <= lower < upper <= 1")
    if hard_lower_rate >= hard_upper_rate:
        raise ValueError("hard_lower_rate must be less than hard_upper_rate")

    raw_rates = raw_rates.copy()
    if "EXCLUSION_REASONS" not in raw_rates.columns:
        raw_rates["EXCLUSION_REASONS"] = pd.NA

    national = national_rates[["OCC_CODE", "WAGE_METRIC", "RAW_RATE", "VALID_TRANSITIONS"]].rename(
        columns={"RAW_RATE": "NATIONAL_RATE", "VALID_TRANSITIONS": "NATIONAL_TRANSITIONS"}
    )
    state = state_rates[
        ["PRIM_STATE", "OCC_CODE", "WAGE_METRIC", "RAW_RATE", "VALID_TRANSITIONS"]
    ].rename(columns={"RAW_RATE": "STATE_RAW_RATE", "VALID_TRANSITIONS": "STATE_TRANSITIONS"})
    state = state.merge(national, on=["OCC_CODE", "WAGE_METRIC"], how="left")
    state["STATE_WEIGHT"] = state["STATE_TRANSITIONS"].fillna(0) / (
        state["STATE_TRANSITIONS"].fillna(0) + shrinkage_k
    )
    state["STATE_CONSTRAINED_RATE"] = (
        state["STATE_WEIGHT"] * state["STATE_RAW_RATE"]
        + (1 - state["STATE_WEIGHT"]) * state["NATIONAL_RATE"]
    )
    state["STATE_CONSTRAINED_RATE"] = state["STATE_CONSTRAINED_RATE"].fillna(
        state["NATIONAL_RATE"]
    )

    audit = raw_rates.merge(
        state[
            [
                "PRIM_STATE",
                "OCC_CODE",
                "WAGE_METRIC",
                "STATE_RAW_RATE",
                "STATE_TRANSITIONS",
                "STATE_WEIGHT",
                "STATE_CONSTRAINED_RATE",
            ]
        ],
        on=["PRIM_STATE", "OCC_CODE", "WAGE_METRIC"],
        how="left",
    )
    audit = audit.merge(
        national,
        on=["OCC_CODE", "WAGE_METRIC"],
        how="left",
    )
    audit["STATE_CONSTRAINED_RATE"] = pd.to_numeric(
        audit["STATE_CONSTRAINED_RATE"], errors="coerce"
    )
    audit["STATE_CONSTRAINED_RATE"] = audit["STATE_CONSTRAINED_RATE"].fillna(
        audit["NATIONAL_RATE"]
    )
    audit["LOCAL_WEIGHT"] = audit["VALID_TRANSITIONS"].fillna(0) / (
        audit["VALID_TRANSITIONS"].fillna(0) + shrinkage_k
    )
    audit["HIERARCHICAL_RATE"] = (
        audit["LOCAL_WEIGHT"] * audit["RAW_RATE"]
        + (1 - audit["LOCAL_WEIGHT"]) * audit["STATE_CONSTRAINED_RATE"]
    )
    audit["HIERARCHICAL_RATE"] = audit["HIERARCHICAL_RATE"].fillna(
        audit["STATE_CONSTRAINED_RATE"]
    ).fillna(audit["NATIONAL_RATE"])

    peers = audit.groupby(["OCC_CODE", "WAGE_METRIC"])["RAW_RATE"].agg(
        PEER_LOWER_RATE=lambda values: values.quantile(peer_lower_quantile),
        PEER_UPPER_RATE=lambda values: values.quantile(peer_upper_quantile),
    ).reset_index()
    audit = audit.merge(peers, on=["OCC_CODE", "WAGE_METRIC"], how="left")
    peer_bounded = audit["HIERARCHICAL_RATE"].clip(
        lower=audit["PEER_LOWER_RATE"], upper=audit["PEER_UPPER_RATE"]
    )
    hard_floor = peer_bounded.lt(hard_lower_rate)
    hard_cap = peer_bounded.gt(hard_upper_rate)
    audit["CONSTRAINED_RATE"] = peer_bounded.clip(hard_lower_rate, hard_upper_rate)

    reasons = []
    for position, row in enumerate(audit.itertuples(index=False)):
        row_reasons = []
        if pd.isna(row.RAW_RATE):
            row_reasons.append("raw_unavailable")
        elif row.LOCAL_WEIGHT < 1:
            row_reasons.append("shrunk_to_state")
        if pd.isna(row.STATE_RAW_RATE):
            row_reasons.append("state_raw_unavailable")
        elif row.STATE_WEIGHT < 1:
            row_reasons.append("state_shrunk_to_national")
        if pd.notna(row.PEER_LOWER_RATE) and row.HIERARCHICAL_RATE < row.PEER_LOWER_RATE:
            row_reasons.append("peer_floor")
        if pd.notna(row.PEER_UPPER_RATE) and row.HIERARCHICAL_RATE > row.PEER_UPPER_RATE:
            row_reasons.append("peer_cap")
        if hard_floor.iloc[position]:
            row_reasons.append("hard_floor")
        if hard_cap.iloc[position]:
            row_reasons.append("hard_cap")
        if pd.notna(row.EXCLUSION_REASONS):
            row_reasons.extend(
                f"excluded_{reason}" for reason in row.EXCLUSION_REASONS.split("|")
            )
        reasons.append("|".join(row_reasons) or "none")
    audit["ADJUSTMENT_REASONS"] = reasons
    audit["HARD_LOWER_RATE"] = hard_lower_rate
    audit["HARD_UPPER_RATE"] = hard_upper_rate
    audit["SHRINKAGE_K"] = shrinkage_k
    audit["CONFIDENCE"] = audit["VALID_TRANSITIONS"].map(
        {4: "high", 3: "medium", 2: "medium", 1: "low", 0: "benchmark_only"}
    ).fillna("benchmark_only")
    return audit


def enforce_percentile_rate_order(
    audit: pd.DataFrame,
    base_wages: pd.DataFrame,
    projection_horizon: int,
) -> pd.DataFrame:
    if projection_horizon <= 0:
        raise ValueError("projection_horizon must be greater than zero")

    ordered_metrics = ["H_PCT10", "H_PCT25", "H_MEDIAN", "H_PCT75", "H_PCT90"]
    identifiers = ["AREA", "OCC_CODE"]
    base_long = base_wages[identifiers + ordered_metrics].melt(
        id_vars=identifiers,
        value_vars=ordered_metrics,
        var_name="WAGE_METRIC",
        value_name="BASE_WAGE",
    )
    result = audit.merge(base_long, on=[*identifiers, "WAGE_METRIC"], how="left")
    rate_matrix = result.pivot(
        index=identifiers, columns="WAGE_METRIC", values="CONSTRAINED_RATE"
    )
    wage_matrix = result.pivot(
        index=identifiers, columns="WAGE_METRIC", values="BASE_WAGE"
    )

    adjusted = pd.DataFrame(False, index=rate_matrix.index, columns=ordered_metrics)
    adjusted.columns.name = "WAGE_METRIC"
    for lower_metric, upper_metric in zip(ordered_metrics, ordered_metrics[1:]):
        minimum_upper_rate = (
            (wage_matrix[lower_metric] / wage_matrix[upper_metric])
            ** (1 / projection_horizon)
            * (1 + rate_matrix[lower_metric])
            - 1
        )
        needs_adjustment = rate_matrix[upper_metric].lt(minimum_upper_rate)
        rate_matrix.loc[needs_adjustment, upper_metric] = minimum_upper_rate.loc[
            needs_adjustment
        ]
        adjusted.loc[needs_adjustment, upper_metric] = True

    adjusted_rates = rate_matrix.stack().rename("ORDERED_RATE").reset_index()
    adjusted_flags = adjusted.stack().rename("ORDER_ADJUSTED").reset_index()
    result = result.merge(adjusted_rates, on=[*identifiers, "WAGE_METRIC"], how="left")
    result = result.merge(adjusted_flags, on=[*identifiers, "WAGE_METRIC"], how="left")
    result["PRE_ORDER_CONSTRAINED_RATE"] = result["CONSTRAINED_RATE"]
    percentile_rows = result["WAGE_METRIC"].isin(ordered_metrics)
    result.loc[percentile_rows, "CONSTRAINED_RATE"] = result.loc[
        percentile_rows, "ORDERED_RATE"
    ]
    if {"HARD_LOWER_RATE", "HARD_UPPER_RATE"}.issubset(result.columns):
        result["CONSTRAINED_RATE"] = result["CONSTRAINED_RATE"].clip(
            lower=result["HARD_LOWER_RATE"],
            upper=result["HARD_UPPER_RATE"],
        )
    changed = result["ORDER_ADJUSTED"].eq(True)
    result.loc[changed, "ADJUSTMENT_REASONS"] = result.loc[
        changed, "ADJUSTMENT_REASONS"
    ].map(
        lambda reasons: (
            "percentile_order_constraint"
            if reasons == "none"
            else f"{reasons}|percentile_order_constraint"
        )
    )
    return result.drop(columns=["ORDERED_RATE", "ORDER_ADJUSTED", "BASE_WAGE"])


def build_labor_escalation_outputs(
    annual_frames: Mapping[int, Mapping[str, pd.DataFrame]],
    base_wages: pd.DataFrame,
    *,
    base_year: int = 2025,
    projection_start: int = 2026,
    projection_end: int = 2050,
    shrinkage_k: float = 4.0,
    peer_lower_quantile: float = 0.10,
    peer_upper_quantile: float = 0.90,
    hard_lower_rate: float = -0.02,
    hard_upper_rate: float = 0.08,
    model_version: str = "labor-oews-2021-2025-v1",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    panel = build_historical_panel(annual_frames, base_wages)
    history = build_transition_audit(panel)
    raw_rates = summarize_raw_rates(history)
    metadata = panel[
        [
            "AREA",
            "AREA_TITLE",
            "PRIM_STATE",
            "GEOGRAPHY_TYPE",
            "OCC_CODE",
            "OCC_TITLE",
            "WAGE_METRIC",
        ]
    ].drop_duplicates()
    raw_rates = metadata.merge(raw_rates, on=RATE_KEYS, how="left")
    raw_rates["TRANSITIONS_TOTAL"] = raw_rates["TRANSITIONS_TOTAL"].fillna(0).astype(int)
    raw_rates["VALID_TRANSITIONS"] = raw_rates["VALID_TRANSITIONS"].fillna(0).astype(int)
    raw_rates["EXCLUDED_TRANSITIONS"] = raw_rates["EXCLUDED_TRANSITIONS"].fillna(0).astype(int)

    state_rates, national_rates = build_benchmark_rates(annual_frames)
    audit = constrain_rates(
        raw_rates,
        state_rates,
        national_rates,
        shrinkage_k=shrinkage_k,
        peer_lower_quantile=peer_lower_quantile,
        peer_upper_quantile=peer_upper_quantile,
        hard_lower_rate=hard_lower_rate,
        hard_upper_rate=hard_upper_rate,
    )
    audit = enforce_percentile_rate_order(
        audit,
        base_wages,
        projection_end - base_year,
    )
    audit["HISTORY_START"] = min(annual_frames)
    audit["HISTORY_END"] = max(annual_frames)
    audit["BASE_YEAR"] = base_year
    audit["MODEL_VERSION"] = model_version

    projections = build_projection_table(
        base_wages,
        audit,
        base_year=base_year,
        projection_start=projection_start,
        projection_end=projection_end,
    )
    projections["BASE_YEAR"] = base_year
    projections["MODEL_VERSION"] = model_version
    history["MODEL_VERSION"] = model_version
    return projections.reset_index(drop=True), audit.reset_index(drop=True), history


def build_projection_table(
    base_wages: pd.DataFrame,
    rate_audit: pd.DataFrame,
    *,
    base_year: int = 2025,
    projection_start: int = 2026,
    projection_end: int = 2050,
) -> pd.DataFrame:
    identifiers = [
        "AREA",
        "AREA_TITLE",
        "PRIM_STATE",
        "GEOGRAPHY_TYPE",
        "OCC_CODE",
        "OCC_TITLE",
    ]
    base_long = base_wages[identifiers + WAGE_METRICS].melt(
        id_vars=identifiers,
        value_vars=WAGE_METRICS,
        var_name="WAGE_METRIC",
        value_name="BASE_WAGE",
    )
    rates = rate_audit[RATE_KEYS + ["RAW_RATE", "CONSTRAINED_RATE"]]
    projected = base_long.merge(rates, on=RATE_KEYS, how="left")
    years = pd.DataFrame({"PROJECTION_YEAR": range(projection_start, projection_end + 1)})
    projected = projected.merge(years, how="cross")
    horizon = projected["PROJECTION_YEAR"] - base_year
    projected["RAW_FACTOR"] = (1 + projected["RAW_RATE"]) ** horizon
    projected["CONSTRAINED_FACTOR"] = (1 + projected["CONSTRAINED_RATE"]) ** horizon
    projected["RAW_PROJECTED_WAGE"] = projected["BASE_WAGE"] * projected["RAW_FACTOR"]
    projected["CONSTRAINED_PROJECTED_WAGE"] = (
        projected["BASE_WAGE"] * projected["CONSTRAINED_FACTOR"]
    )

    value_columns = [
        "BASE_WAGE",
        "RAW_FACTOR",
        "RAW_PROJECTED_WAGE",
        "CONSTRAINED_FACTOR",
        "CONSTRAINED_PROJECTED_WAGE",
    ]
    wide = projected.pivot(
        index=[*identifiers, "PROJECTION_YEAR"],
        columns="WAGE_METRIC",
        values=value_columns,
    )
    wide.columns = [f"{metric}_{value}" for value, metric in wide.columns]
    return wide.reset_index().sort_values(["AREA", "OCC_CODE", "PROJECTION_YEAR"])