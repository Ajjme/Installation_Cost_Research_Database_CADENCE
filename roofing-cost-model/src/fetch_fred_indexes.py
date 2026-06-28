"""Fetch monthly public price-index series from FRED for material escalation.

This module reads ``config/bls_fred_series.yml``, pulls monthly observations
for each configured series from FRED, writes the raw payloads to
``data_raw/fred/`` for auditability, and writes a single normalized table to
``data_intermediate/fred_indexes.parquet``.

Two fetch paths are supported:

  * **API path** (preferred): if a ``FRED_API_KEY`` environment variable is
    set, observations are pulled from the FRED JSON API.
  * **CSV fallback**: if no API key is available, the public
    ``fredgraph.csv`` download endpoint is used (no key required).

The indexes are *national* monthly inflation indexes. They carry no geography
and are used only for time adjustment of retailer-scraped prices.

Usage:
    # API path (set the key first):
    #   PowerShell:  $env:FRED_API_KEY = "your_key"
    python -m src.fetch_fred_indexes

    # Force the keyless public CSV fallback:
    python -m src.fetch_fred_indexes --no-api

Normalized output columns:
    series_id, series_name, date, value, source, material_mapping,
    use_case, priority, pulled_at
"""

from __future__ import annotations

import argparse
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests
import yaml

logger = logging.getLogger(__name__)

# Repository-relative default locations. Resolved from this file so the module
# works regardless of the current working directory.
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "bls_fred_series.yml"
DEFAULT_RAW_DIR = _PROJECT_ROOT / "data_raw" / "fred"
DEFAULT_OUTPUT_PATH = _PROJECT_ROOT / "data_intermediate" / "fred_indexes.parquet"

# FRED endpoints.
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"

# Normalized output schema (column order matters for stable parquet writes).
OUTPUT_COLUMNS = [
    "series_id",
    "series_name",
    "date",
    "value",
    "source",
    "material_mapping",
    "use_case",
    "priority",
    "pulled_at",
]

# Sentinel FRED uses for "no observation".
_FRED_MISSING = {".", "", "NA", "NaN", "nan", None}

DEFAULT_TIMEOUT = 30


class FredFetchError(RuntimeError):
    """Raised for unrecoverable errors while fetching a FRED series."""


# --------------------------------------------------------------------------- #
# Config loading
# --------------------------------------------------------------------------- #
def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> dict[str, Any]:
    """Load and lightly validate the BLS/FRED series YAML config.

    Args:
        config_path: Path to ``bls_fred_series.yml``.

    Returns:
        Parsed config dict with ``defaults`` and ``series`` keys.

    Raises:
        FredFetchError: If the file is missing or has no ``series`` list.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FredFetchError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        config = yaml.safe_load(fh) or {}

    series = config.get("series")
    if not series:
        raise FredFetchError(f"Config has no 'series' entries: {path}")

    config.setdefault("defaults", {})
    return config


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _coerce_value(raw: Any) -> Optional[float]:
    """Coerce a raw FRED value to float, returning None for missing/nonnumeric."""
    if raw in _FRED_MISSING:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fetch_series_api(
    series_id: str,
    api_key: str,
    *,
    frequency: str = "m",
    aggregation_method: str = "avg",
    observation_start: str = "2000-01-01",
    timeout: int = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch one series from the FRED JSON API.

    Returns:
        DataFrame with columns ``date`` (datetime) and ``value`` (float).

    Raises:
        FredFetchError: On HTTP/network failure or an invalid series ID.
    """
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "frequency": frequency,
        "aggregation_method": aggregation_method,
        "observation_start": observation_start,
    }
    http = session or requests
    try:
        resp = http.get(FRED_API_URL, params=params, timeout=timeout)
    except requests.RequestException as exc:  # network failure
        raise FredFetchError(f"Network error fetching {series_id}: {exc}") from exc

    if resp.status_code == 400:
        # FRED returns 400 with an explanatory message for bad series IDs.
        raise FredFetchError(f"Invalid series ID '{series_id}': {resp.text[:200]}")
    if resp.status_code != 200:
        raise FredFetchError(
            f"FRED API returned HTTP {resp.status_code} for {series_id}: "
            f"{resp.text[:200]}"
        )

    payload = resp.json()
    observations = payload.get("observations", [])
    rows = [
        {"date": obs.get("date"), "value": _coerce_value(obs.get("value"))}
        for obs in observations
    ]
    return _frame_from_rows(rows)


def fetch_series_csv(
    series_id: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch one series from the public keyless ``fredgraph.csv`` endpoint.

    Returns:
        DataFrame with columns ``date`` (datetime) and ``value`` (float).

    Raises:
        FredFetchError: On HTTP/network failure or an invalid series ID.
    """
    http = session or requests
    try:
        resp = http.get(FRED_CSV_URL, params={"id": series_id}, timeout=timeout)
    except requests.RequestException as exc:
        raise FredFetchError(f"Network error fetching {series_id}: {exc}") from exc

    if resp.status_code != 200 or not resp.text.strip():
        raise FredFetchError(
            f"CSV fallback failed for '{series_id}' (HTTP {resp.status_code})."
        )

    try:
        raw = pd.read_csv(io.StringIO(resp.text))
    except (pd.errors.ParserError, ValueError) as exc:
        raise FredFetchError(f"Could not parse CSV for {series_id}: {exc}") from exc

    # fredgraph.csv has a date column ("DATE" or "observation_date") plus a
    # value column named after the series ID.
    date_col = next(
        (c for c in raw.columns if c.lower() in {"date", "observation_date"}),
        raw.columns[0],
    )
    value_col = next((c for c in raw.columns if c != date_col), None)
    if value_col is None:
        raise FredFetchError(f"CSV for {series_id} had no value column.")

    rows = [
        {"date": d, "value": _coerce_value(v)}
        for d, v in zip(raw[date_col], raw[value_col])
    ]
    return _frame_from_rows(rows)


def _frame_from_rows(rows: list[dict[str, Any]]) -> pd.DataFrame:
    """Build a clean (date, value) frame: parse dates, drop nonnumeric values."""
    df = pd.DataFrame(rows, columns=["date", "value"])
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"]).sort_values("date").reset_index(drop=True)
    return df


def fetch_one(
    entry: dict[str, Any],
    defaults: dict[str, Any],
    *,
    api_key: Optional[str],
    use_api: bool,
    raw_dir: Path,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch a single configured series, write its raw CSV, and normalize it.

    Returns:
        Normalized frame following ``OUTPUT_COLUMNS`` (may be empty).
    """
    series_id = entry["series_id"]
    if not entry.get("verified", False):
        logger.warning(
            "Series '%s' is marked verified: false — confirm the ID in FRED/BLS.",
            series_id,
        )

    if use_api and api_key:
        obs = fetch_series_api(
            series_id,
            api_key,
            frequency=defaults.get("frequency", "m"),
            aggregation_method=defaults.get("aggregation_method", "avg"),
            observation_start=defaults.get("observation_start", "2000-01-01"),
            session=session,
        )
    else:
        obs = fetch_series_csv(series_id, session=session)

    # Persist the raw observations for audit before any further processing.
    _write_raw(series_id, obs, raw_dir)

    if obs.empty:
        logger.warning("Series '%s' returned no usable observations.", series_id)
        return pd.DataFrame(columns=OUTPUT_COLUMNS)

    pulled_at = datetime.now(timezone.utc).isoformat()
    out = obs.copy()
    out["series_id"] = series_id
    out["series_name"] = entry.get("series_name", "")
    out["source"] = entry.get("source", "fred")
    # material_mapping is a list -> stored as a single object cell per row so a
    # series mapping to multiple material classes is preserved losslessly.
    mapping = list(entry.get("material_mapping", []))
    out["material_mapping"] = [mapping] * len(out)
    out["use_case"] = entry.get("use_case", "")
    out["priority"] = entry.get("priority")
    out["pulled_at"] = pulled_at
    return out[OUTPUT_COLUMNS]


def _write_raw(series_id: str, obs: pd.DataFrame, raw_dir: Path) -> None:
    """Write a per-series raw CSV snapshot (overwrite-safe, rerun friendly)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    raw_path = raw_dir / f"{series_id}.csv"
    obs.to_csv(raw_path, index=False)
    logger.debug("Wrote raw observations: %s (%d rows)", raw_path, len(obs))


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def fetch_all(
    config: dict[str, Any],
    *,
    api_key: Optional[str],
    use_api: bool,
    raw_dir: Path = DEFAULT_RAW_DIR,
    session: Optional[requests.Session] = None,
) -> pd.DataFrame:
    """Fetch every configured series and return a combined normalized frame.

    Individual series failures are logged and skipped so one bad series ID
    does not abort the whole pull.
    """
    defaults = config.get("defaults", {})
    frames: list[pd.DataFrame] = []
    for entry in config["series"]:
        series_id = entry.get("series_id", "<missing>")
        try:
            frame = fetch_one(
                entry,
                defaults,
                api_key=api_key,
                use_api=use_api,
                raw_dir=raw_dir,
                session=session,
            )
        except FredFetchError as exc:
            logger.error("Skipping series '%s': %s", series_id, exc)
            continue
        if not frame.empty:
            frames.append(frame)
            logger.info("Fetched %d rows for series '%s'.", len(frame), series_id)

    if not frames:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    return combined


def write_normalized(df: pd.DataFrame, output_path: Path = DEFAULT_OUTPUT_PATH) -> None:
    """Write the normalized frame to Parquet (idempotent overwrite)."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False)
    logger.info("Wrote normalized indexes: %s (%d rows)", output_path, len(df))


def run(
    *,
    config_path: Path = DEFAULT_CONFIG_PATH,
    raw_dir: Path = DEFAULT_RAW_DIR,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    use_api: bool = True,
    api_key: Optional[str] = None,
) -> pd.DataFrame:
    """End-to-end fetch: load config, pull all series, write outputs.

    Args:
        config_path: Path to the series YAML config.
        raw_dir: Directory for per-series raw CSV snapshots.
        output_path: Destination Parquet path for normalized data.
        use_api: If True and a key is available, use the FRED API; otherwise
            use the keyless public CSV fallback.
        api_key: FRED API key; falls back to the ``FRED_API_KEY`` env var.

    Returns:
        The combined normalized frame that was written.
    """
    config = load_config(config_path)

    resolved_key = api_key or os.environ.get("FRED_API_KEY")
    if use_api and not resolved_key:
        logger.warning(
            "No FRED_API_KEY found; falling back to the public CSV endpoint. "
            "Set FRED_API_KEY for higher reliability and rate limits."
        )
        use_api = False

    df = fetch_all(
        config,
        api_key=resolved_key,
        use_api=use_api,
        raw_dir=raw_dir,
    )
    write_normalized(df, output_path)
    return df


def main(argv: Optional[list[str]] = None) -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Fetch monthly FRED/BLS price indexes for material escalation."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to bls_fred_series.yml.",
    )
    parser.add_argument(
        "--raw-dir",
        default=str(DEFAULT_RAW_DIR),
        help="Directory for raw per-series CSV snapshots.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Normalized Parquet output path.",
    )
    parser.add_argument(
        "--no-api",
        action="store_true",
        help="Force the keyless public CSV fallback even if FRED_API_KEY is set.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    df = run(
        config_path=Path(args.config),
        raw_dir=Path(args.raw_dir),
        output_path=Path(args.output),
        use_api=not args.no_api,
    )
    logger.info(
        "Done. %d total rows across %d series.",
        len(df),
        df["series_id"].nunique() if not df.empty else 0,
    )


if __name__ == "__main__":
    main()
