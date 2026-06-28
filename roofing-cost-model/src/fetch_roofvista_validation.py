"""RoofVista validation data pipeline.

Collects installed roofing estimates from RoofVista's public API and stores:
- Raw request/response records (JSON + SQLite)
- Normalized tabular estimates (SQLite + CSV + Parquet)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent

DEFAULT_BASE_URL = "https://roofvista.com/api/v1/public/pricing"
DEFAULT_STATES = ["MA", "CT", "RI", "NH", "VT", "ME"]
DEFAULT_MATERIALS = [
    "3-tab shingles",
    "architectural shingles",
    "premium shingles",
    "tile",
    "standing seam metal",
]

MATERIAL_PARAM_CANDIDATES = {
    "3-tab shingles": ["3-tab", "3_tab", "3tab", "three-tab", "3-tab shingles"],
    "architectural shingles": ["architectural", "architectural-shingles", "architectural shingles"],
    "premium shingles": ["premium", "premium-shingles", "premium shingles"],
    "tile": ["tile", "tile-roof"],
    "standing seam metal": [
        "standing-seam-metal",
        "standing_seam_metal",
        "standing-seam",
        "standing seam metal",
        "metal",
    ],
}

MATERIAL_ALIASES = {
    "3-tab": "3-tab shingles",
    "3_tab": "3-tab shingles",
    "3tab": "3-tab shingles",
    "three-tab": "3-tab shingles",
    "architectural": "architectural shingles",
    "premium": "premium shingles",
    "standing-seam-metal": "standing seam metal",
    "standing_seam_metal": "standing seam metal",
    "standing-seam": "standing seam metal",
    "metal": "standing seam metal",
}

DEFAULT_SAMPLE_CONFIG = _PROJECT_ROOT / "config" / "roofvista_sample_locations.csv"
DEFAULT_SCHEMA_DIR = _PROJECT_ROOT / "data_raw" / "roofvista" / "schema_discovery"
DEFAULT_RAW_DIR = _PROJECT_ROOT / "data_raw" / "roofvista" / "responses"
DEFAULT_DB_PATH = _PROJECT_ROOT / "data_output" / "roofvista" / "roofvista_validation.sqlite"
DEFAULT_CSV_PATH = _PROJECT_ROOT / "data_output" / "roofvista" / "roofvista_validation_estimates.csv"
DEFAULT_PARQUET_PATH = _PROJECT_ROOT / "data_output" / "roofvista" / "roofvista_validation_estimates.parquet"

SAMPLE_COLUMNS = [
    "sample_id",
    "state",
    "city",
    "zip_code",
    "address",
    "latitude",
    "longitude",
    "source",
]

NORMALIZED_COLUMNS = [
    "run_id",
    "sample_id",
    "requested_state",
    "requested_city",
    "requested_zip_code",
    "requested_address",
    "latitude",
    "longitude",
    "material_tier_requested",
    "material_tier_returned",
    "estimated_cost_per_square",
    "estimated_cost_per_sqft",
    "installed_cost_per_square",
    "material_cost_per_square",
    "labor_cost_per_square",
    "material_share",
    "labor_share",
    "min_cost_per_square",
    "max_cost_per_square",
    "currency",
    "geographic_resolution",
    "api_effective_date",
    "api_response_timestamp",
    "request_url",
    "http_status",
    "parse_status",
    "parse_error",
    "raw_response_path",
]

COST_COLUMNS = [
    "estimated_cost_per_square",
    "estimated_cost_per_sqft",
    "installed_cost_per_square",
    "material_cost_per_square",
    "labor_cost_per_square",
    "min_cost_per_square",
    "max_cost_per_square",
]

FALLBACK_SEEDS = {
    "MA": [("Boston", "02108"), ("Worcester", "01608"), ("Springfield", "01103")],
    "CT": [("Hartford", "06103"), ("New Haven", "06510"), ("Bridgeport", "06604")],
    "RI": [("Providence", "02903"), ("Warwick", "02886"), ("Cranston", "02910")],
    "NH": [("Manchester", "03101"), ("Nashua", "03060"), ("Concord", "03301")],
    "VT": [("Burlington", "05401"), ("Rutland", "05701"), ("Barre", "05641")],
    "ME": [("Portland", "04101"), ("Lewiston", "04240"), ("Bangor", "04401")],
}


@dataclass
class APIResult:
    request_url: str
    request_params: dict[str, Any]
    request_hash: str
    http_status: int | None
    response_timestamp: str
    response_json: Any
    error: str | None


@dataclass
class RequestRecord:
    run_id: str
    request_hash: str
    sample_id: str | None
    requested_state: str
    requested_city: str | None
    requested_zip_code: str | None
    requested_address: str | None
    latitude: float | None
    longitude: float | None
    material_tier_requested: str
    requested_material_param: str
    geographic_resolution: str
    request_url: str
    request_params: dict[str, Any]
    http_status: int | None
    response_timestamp: str
    raw_response_path: str
    response_json: Any
    error: str | None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def canonical_query(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None and str(v).strip() != ""}


def build_full_url(base_url: str, params: dict[str, Any]) -> str:
    query = canonical_query(params)
    return base_url if not query else f"{base_url}?{urlencode(query)}"


def stable_request_hash(base_url: str, params: dict[str, Any]) -> str:
    raw = json.dumps({"url": base_url, "params": canonical_query(params)}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text.startswith("$"):
        text = text[1:]
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        cleaned = "".join(ch for ch in text if ch.isdigit() or ch in ".-")
        if cleaned in {"", "-", ".", "-."}:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=True)


def flatten_json(node: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            out.update(flatten_json(v, key))
        return out
    if isinstance(node, list):
        for idx, v in enumerate(node):
            key = f"{prefix}[{idx}]" if prefix else f"[{idx}]"
            out.update(flatten_json(v, key))
        return out
    out[prefix or "value"] = node
    return out


def find_first_value(flat: dict[str, Any], all_tokens: list[str], any_tokens: list[str] | None = None) -> Any:
    needs_all = [t.lower() for t in all_tokens]
    needs_any = [t.lower() for t in (any_tokens or [])]
    for key, value in flat.items():
        lk = key.lower()
        if not all(t in lk for t in needs_all):
            continue
        if needs_any and not any(t in lk for t in needs_any):
            continue
        return value
    return None


def infer_geo_resolution(params: dict[str, Any]) -> str:
    if params.get("address"):
        return "address"
    if params.get("zip") or params.get("zip_code"):
        return "zip"
    if params.get("city") or params.get("municipality"):
        return "city"
    return "state"


class RoofVistaClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        max_retries: int,
        backoff_factor: float,
        rate_limit_seconds: float,
    ) -> None:
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self.rate_limit_seconds = rate_limit_seconds
        self._last_request_time = 0.0
        retry = Retry(
            total=max_retries,
            connect=max_retries,
            read=max_retries,
            status=max_retries,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET"],
            backoff_factor=backoff_factor,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session = requests.Session()
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update({"Accept": "application/json", "User-Agent": "roofvista-validation-pipeline/1.0"})

    def _sleep(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_seconds:
            time.sleep(max(0.0, self.rate_limit_seconds - elapsed))

    def fetch(self, params: dict[str, Any]) -> APIResult:
        params = canonical_query(params)
        request_url = build_full_url(self.base_url, params)
        request_hash = stable_request_hash(self.base_url, params)
        ts = utc_now_iso()

        self._sleep()
        try:
            response = self.session.get(self.base_url, params=params, timeout=self.timeout_seconds)
            self._last_request_time = time.time()
            try:
                payload = response.json()
                err = None
            except ValueError as exc:
                payload = {"_non_json_response": response.text[:5000]}
                err = f"json_decode_error: {exc}"
            return APIResult(request_url, params, request_hash, response.status_code, ts, payload, err)
        except requests.RequestException as exc:
            return APIResult(request_url, params, request_hash, None, ts, None, str(exc))


def normalize_material_tier(value: str) -> str:
    clean = str(value).strip().lower()
    if clean in MATERIAL_ALIASES:
        return MATERIAL_ALIASES[clean]
    for m in DEFAULT_MATERIALS:
        if clean == m.lower():
            return m
    return str(value).strip()


def read_seed_locations(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=SAMPLE_COLUMNS)
    df = pd.read_csv(path, dtype=str).fillna("")
    for col in SAMPLE_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    df = df[SAMPLE_COLUMNS]
    df["state"] = df["state"].astype(str).str.upper().str.strip()
    return df


def fallback_rows_for_state(state: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for i, (city, zip_code) in enumerate(FALLBACK_SEEDS.get(state, []), start=1):
        rows.append(
            {
                "sample_id": f"{state}-FALLBACK-{i:03d}",
                "state": state,
                "city": city,
                "zip_code": zip_code,
                "address": "",
                "latitude": "",
                "longitude": "",
                "source": "fallback_static_city_zip",
            }
        )
    return rows


def generate_sample_locations(seed_df: pd.DataFrame, states: list[str], sample_size: int, random_seed: int) -> pd.DataFrame:
    rng = random.Random(random_seed)
    rows: list[dict[str, Any]] = []
    for state in states:
        state_df = seed_df[seed_df["state"] == state].copy() if not seed_df.empty else pd.DataFrame(columns=SAMPLE_COLUMNS)
        pool = state_df.to_dict(orient="records") if not state_df.empty else fallback_rows_for_state(state)
        if not pool:
            logger.warning("No sample location pool for state=%s", state)
            continue
        for i in range(sample_size):
            pick = dict(rng.choice(pool))
            pick["sample_id"] = f"{state}-{i + 1:04d}"
            pick["state"] = state
            rows.append(pick)
    out = pd.DataFrame(rows, columns=SAMPLE_COLUMNS)
    if out.empty:
        return out
    out["latitude"] = pd.to_numeric(out["latitude"], errors="coerce")
    out["longitude"] = pd.to_numeric(out["longitude"], errors="coerce")
    return out


def build_param_candidates(sample_row: dict[str, Any], state: str, material_param: str) -> list[tuple[dict[str, Any], str]]:
    base = {"state": state, "material": material_param}
    city = str(sample_row.get("city", "")).strip()
    zip_code = str(sample_row.get("zip_code", "")).strip()
    address = str(sample_row.get("address", "")).strip()
    lat = sample_row.get("latitude")
    lon = sample_row.get("longitude")

    options: list[tuple[dict[str, Any], str]] = []
    if address and city and zip_code:
        options.append(({**base, "address": address, "city": city, "zip": zip_code}, "address"))
    if zip_code and city:
        options.append(({**base, "zip": zip_code, "city": city}, "zip"))
    if zip_code:
        options.append(({**base, "zip": zip_code}, "zip"))
    if city:
        options.append(({**base, "city": city}, "city"))
    if pd.notna(lat) and pd.notna(lon):
        options.append(({**base, "latitude": lat, "longitude": lon}, "coordinate"))
    options.append(({"state": state, "material": material_param}, "state"))

    deduped: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for params, res in options:
        key = build_full_url(DEFAULT_BASE_URL, params)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((params, res))
    return deduped


def make_raw_filename(run_id: str, sample_id: str | None, material_tier: str) -> str:
    s = sample_id or "schema"
    m = material_tier.replace(" ", "_").replace("/", "_")
    return f"{run_id}_{s}_{m}.json"


def save_api_result(raw_dir: Path, run_id: str, sample_id: str | None, material_tier: str, api_result: APIResult) -> Path:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / make_raw_filename(run_id, sample_id, material_tier)
    payload = {
        "request_url": api_result.request_url,
        "request_params": api_result.request_params,
        "request_hash": api_result.request_hash,
        "http_status": api_result.http_status,
        "response_timestamp": api_result.response_timestamp,
        "error": api_result.error,
        "response": api_result.response_json,
    }
    write_json(path, payload)
    return path


def discover_schema(client: RoofVistaClient, states: list[str], out_dir: Path, run_id: str) -> list[dict[str, Any]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summaries: list[dict[str, Any]] = []
    for state in states:
        api = client.fetch({"state": state})
        raw_path = save_api_result(out_dir, run_id, None, f"schema_{state}", api)
        flat = flatten_json(api.response_json)
        top_keys = sorted(api.response_json.keys()) if isinstance(api.response_json, dict) else []
        material_values = sorted(
            {
                str(v)
                for k, v in flat.items()
                if any(token in k.lower() for token in ["material", "tier"]) and v is not None
            }
        )[:50]
        cost_fields = [k for k in flat if any(t in k.lower() for t in ["cost", "price", "labor", "material"])][:120]
        date_fields = [k for k in flat if any(t in k.lower() for t in ["date", "effective", "updated"])][:60]
        summary = {
            "state": state,
            "http_status": api.http_status,
            "request_url": api.request_url,
            "raw_response_path": str(raw_path),
            "top_level_keys": top_keys,
            "material_like_values": material_values,
            "cost_like_fields": cost_fields,
            "effective_date_like_fields": date_fields,
            "error": api.error,
        }
        summaries.append(summary)
        logger.info("Schema %s status=%s keys=%s", state, api.http_status, top_keys)
    return summaries


def normalize_record(record: RequestRecord) -> dict[str, Any]:
    payload = record.response_json if isinstance(record.response_json, (dict, list)) else {}
    flat = flatten_json(payload)

    # RoofVista can return either:
    # 1) material-specific fields at top level when material query param is used
    # 2) state-level "materials" object keyed by material code when only state is queried
    requested_material_key = str(record.requested_material_param or "").strip().lower()
    material_payload: dict[str, Any] = {}
    if isinstance(payload, dict):
        materials_node = payload.get("materials")
        if isinstance(materials_node, dict) and requested_material_key:
            node = materials_node.get(requested_material_key)
            if isinstance(node, dict):
                material_payload = node
            elif requested_material_key in MATERIAL_ALIASES:
                alias = MATERIAL_ALIASES[requested_material_key].lower()
                for key, value in materials_node.items():
                    if normalize_material_tier(str(key)).lower() == alias and isinstance(value, dict):
                        material_payload = value
                        break

    # Prefer material-scoped values when available, then fall back to full flatten search.
    scoped_flat = flatten_json(material_payload) if material_payload else {}

    def pick_float(*candidates: str) -> float | None:
        for key in candidates:
            if key in scoped_flat:
                parsed = safe_float(scoped_flat.get(key))
                if parsed is not None:
                    return parsed
            if key in flat:
                parsed = safe_float(flat.get(key))
                if parsed is not None:
                    return parsed
        return None

    def pick_float_fuzzy(*token_sets: tuple[str, ...]) -> float | None:
        for tokens in token_sets:
            value = safe_float(find_first_value(scoped_flat, list(tokens)))
            if value is not None:
                return value
            value = safe_float(find_first_value(flat, list(tokens)))
            if value is not None:
                return value
        return None

    price_per_square = pick_float(
        "price_per_square",
        "estimated_cost_per_square",
        "installed_cost_per_square",
    ) or pick_float_fuzzy(("estimated", "square"), ("installed", "square"), ("price", "square"))
    price_per_sqft = pick_float(
        "price_per_sqft",
        "estimated_cost_per_sqft",
        "installed_cost_per_sqft",
    ) or pick_float_fuzzy(("estimated", "sqft"), ("installed", "sqft"), ("price", "sqft"))
    material_share = pick_float("material_cost_percent", "material_share") or pick_float_fuzzy(("material", "share"))
    labor_share = pick_float("labor_cost_percent", "labor_share") or pick_float_fuzzy(("labor", "share"))

    # Convert percent-like shares (e.g., 55) into fractions (0.55).
    if material_share is not None and material_share > 1:
        material_share = material_share / 100.0
    if labor_share is not None and labor_share > 1:
        labor_share = labor_share / 100.0

    material_cost_per_square = pick_float("material_cost_per_square") or pick_float_fuzzy(("material", "square"))
    labor_cost_per_square = pick_float("labor_cost_per_square") or pick_float_fuzzy(("labor", "square"))
    if price_per_square is not None:
        if material_cost_per_square is None and material_share is not None:
            material_cost_per_square = price_per_square * material_share
        if labor_cost_per_square is None and labor_share is not None:
            labor_cost_per_square = price_per_square * labor_share

    row = {
        "run_id": record.run_id,
        "sample_id": record.sample_id,
        "requested_state": record.requested_state,
        "requested_city": record.requested_city,
        "requested_zip_code": record.requested_zip_code,
        "requested_address": record.requested_address,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "material_tier_requested": record.material_tier_requested,
        "material_tier_returned": find_first_value(flat, [], ["material", "tier", "type"]),
        "estimated_cost_per_square": price_per_square,
        "estimated_cost_per_sqft": price_per_sqft,
        "installed_cost_per_square": pick_float("installed_cost_per_square")
        or pick_float_fuzzy(("installed", "square"))
        or price_per_square,
        "material_cost_per_square": material_cost_per_square,
        "labor_cost_per_square": labor_cost_per_square,
        "material_share": material_share,
        "labor_share": labor_share,
        "min_cost_per_square": safe_float(find_first_value(flat, ["min", "square"])),
        "max_cost_per_square": safe_float(find_first_value(flat, ["max", "square"])),
        "currency": find_first_value(flat, ["currency"]) or "USD",
        "geographic_resolution": record.geographic_resolution,
        "api_effective_date": find_first_value(flat, ["effective"], ["date", "updated", "timestamp"])
        or find_first_value(flat, ["date"]),
        "api_response_timestamp": record.response_timestamp,
        "request_url": record.request_url,
        "http_status": record.http_status,
        "parse_status": "ok",
        "parse_error": None,
        "raw_response_path": record.raw_response_path,
    }

    if record.error:
        row["parse_status"] = "request_failed"
        row["parse_error"] = record.error
        return row

    if record.http_status is None or record.http_status >= 400:
        row["parse_status"] = "http_error"
        row["parse_error"] = f"HTTP status {record.http_status}"
        return row

    if all(row[c] is None for c in COST_COLUMNS):
        row["parse_status"] = "missing_cost_fields"
        row["parse_error"] = "All known cost fields missing"

    return row


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS roofvista_runs (
            run_id TEXT PRIMARY KEY,
            run_timestamp TEXT NOT NULL,
            states_json TEXT NOT NULL,
            materials_json TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            random_seed INTEGER NOT NULL,
            schema_discovery_only INTEGER NOT NULL,
            notes TEXT
        );

        CREATE TABLE IF NOT EXISTS roofvista_sample_locations (
            run_id TEXT NOT NULL,
            sample_id TEXT NOT NULL,
            state TEXT NOT NULL,
            city TEXT,
            zip_code TEXT,
            address TEXT,
            latitude REAL,
            longitude REAL,
            source TEXT,
            PRIMARY KEY (run_id, sample_id)
        );

        CREATE TABLE IF NOT EXISTS roofvista_raw_responses (
            run_id TEXT NOT NULL,
            request_hash TEXT PRIMARY KEY,
            sample_id TEXT,
            requested_state TEXT NOT NULL,
            requested_city TEXT,
            requested_zip_code TEXT,
            requested_address TEXT,
            latitude REAL,
            longitude REAL,
            material_tier_requested TEXT,
            requested_material_param TEXT,
            geographic_resolution TEXT,
            query_params_json TEXT NOT NULL,
            request_url TEXT NOT NULL,
            http_status INTEGER,
            response_timestamp TEXT NOT NULL,
            error TEXT,
            raw_response_path TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS roofvista_normalized_estimates (
            run_id TEXT NOT NULL,
            sample_id TEXT,
            requested_state TEXT NOT NULL,
            requested_city TEXT,
            requested_zip_code TEXT,
            requested_address TEXT,
            latitude REAL,
            longitude REAL,
            material_tier_requested TEXT NOT NULL,
            material_tier_returned TEXT,
            estimated_cost_per_square REAL,
            estimated_cost_per_sqft REAL,
            installed_cost_per_square REAL,
            material_cost_per_square REAL,
            labor_cost_per_square REAL,
            material_share REAL,
            labor_share REAL,
            min_cost_per_square REAL,
            max_cost_per_square REAL,
            currency TEXT,
            geographic_resolution TEXT,
            api_effective_date TEXT,
            api_response_timestamp TEXT,
            request_url TEXT,
            http_status INTEGER,
            parse_status TEXT,
            parse_error TEXT,
            raw_response_path TEXT
        );
        """
    )


def write_run(conn: sqlite3.Connection, run_id: str, states: list[str], materials: list[str], sample_size: int, random_seed: int, schema_discovery_only: bool) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO roofvista_runs (
            run_id, run_timestamp, states_json, materials_json, sample_size, random_seed, schema_discovery_only, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            utc_now_iso(),
            json.dumps(states),
            json.dumps(materials),
            sample_size,
            random_seed,
            1 if schema_discovery_only else 0,
            "RoofVista validation run",
        ),
    )


def write_sample_locations(conn: sqlite3.Connection, run_id: str, sample_df: pd.DataFrame) -> None:
    if sample_df.empty:
        return
    rows = [
        (
            run_id,
            row["sample_id"],
            row.get("state"),
            row.get("city"),
            row.get("zip_code"),
            row.get("address"),
            safe_float(row.get("latitude")),
            safe_float(row.get("longitude")),
            row.get("source"),
        )
        for _, row in sample_df.iterrows()
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO roofvista_sample_locations (
            run_id, sample_id, state, city, zip_code, address, latitude, longitude, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def write_raw_records(conn: sqlite3.Connection, records: list[RequestRecord]) -> None:
    rows = [
        (
            r.run_id,
            r.request_hash,
            r.sample_id,
            r.requested_state,
            r.requested_city,
            r.requested_zip_code,
            r.requested_address,
            r.latitude,
            r.longitude,
            r.material_tier_requested,
            r.requested_material_param,
            r.geographic_resolution,
            json.dumps(r.request_params, default=str),
            r.request_url,
            r.http_status,
            r.response_timestamp,
            r.error,
            r.raw_response_path,
        )
        for r in records
    ]
    conn.executemany(
        """
        INSERT OR REPLACE INTO roofvista_raw_responses (
            run_id, request_hash, sample_id, requested_state, requested_city, requested_zip_code,
            requested_address, latitude, longitude, material_tier_requested, requested_material_param,
            geographic_resolution, query_params_json, request_url, http_status, response_timestamp, error, raw_response_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def write_normalized(conn: sqlite3.Connection, normalized_df: pd.DataFrame) -> None:
    normalized_df.to_sql("roofvista_normalized_estimates", conn, if_exists="append", index=False)


def dedupe_records(records: list[RequestRecord]) -> list[RequestRecord]:
    deduped: dict[str, RequestRecord] = {}
    for r in records:
        deduped.setdefault(r.request_hash, r)
    return list(deduped.values())


def fetch_for_sample_material(client: RoofVistaClient, sample_row: dict[str, Any], state: str, material_tier: str) -> RequestRecord:
    material_params = MATERIAL_PARAM_CANDIDATES.get(material_tier, [material_tier])
    best_record: RequestRecord | None = None

    for material_param in material_params:
        for params, resolution in build_param_candidates(sample_row, state, material_param):
            api = client.fetch(params)
            record = RequestRecord(
                run_id="",
                request_hash=api.request_hash,
                sample_id=sample_row.get("sample_id"),
                requested_state=state,
                requested_city=sample_row.get("city"),
                requested_zip_code=sample_row.get("zip_code"),
                requested_address=sample_row.get("address"),
                latitude=safe_float(sample_row.get("latitude")),
                longitude=safe_float(sample_row.get("longitude")),
                material_tier_requested=material_tier,
                requested_material_param=material_param,
                geographic_resolution=infer_geo_resolution(params),
                request_url=api.request_url,
                request_params=api.request_params,
                http_status=api.http_status,
                response_timestamp=api.response_timestamp,
                raw_response_path="",
                response_json=api.response_json,
                error=api.error,
            )
            best_record = record
            if api.http_status is not None and 200 <= api.http_status < 300 and api.response_json is not None:
                return record

        logger.warning("Material param failed: tier=%s param=%s sample=%s", material_tier, material_param, sample_row.get("sample_id"))

    assert best_record is not None
    return best_record


def run_pipeline(args: argparse.Namespace) -> dict[str, Any]:
    run_id = uuid.uuid4().hex[:12]
    states = [str(s).upper() for s in args.states]
    materials = [normalize_material_tier(m) for m in args.materials]

    db_path = Path(args.db_path)
    csv_path = Path(args.csv_path)
    parquet_path = Path(args.parquet_path)
    raw_dir = Path(args.raw_dir)
    schema_dir = Path(args.schema_dir)

    raw_dir.mkdir(parents=True, exist_ok=True)
    schema_dir.mkdir(parents=True, exist_ok=True)
    ensure_parent(db_path)
    ensure_parent(csv_path)
    ensure_parent(parquet_path)

    client = RoofVistaClient(
        base_url=args.base_url,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        backoff_factor=args.backoff_factor,
        rate_limit_seconds=args.rate_limit_seconds,
    )

    conn = sqlite3.connect(db_path)
    try:
        create_tables(conn)
        write_run(conn, run_id, states, materials, args.sample_size, args.random_seed, args.discover_schema)

        if args.discover_schema:
            summary = discover_schema(client, states, schema_dir, run_id)
            conn.commit()
            return {"run_id": run_id, "schema_discovery": summary}

        seed_df = read_seed_locations(Path(args.sample_config))
        sample_df = generate_sample_locations(seed_df, states, args.sample_size, args.random_seed)
        if sample_df.empty:
            raise RuntimeError("No sample locations generated.")

        write_sample_locations(conn, run_id, sample_df)

        raw_records: list[RequestRecord] = []
        normalized_rows: list[dict[str, Any]] = []

        for _, sample in sample_df.iterrows():
            sample_row = sample.to_dict()
            state = str(sample_row.get("state", "")).upper()
            for material_tier in materials:
                rec = fetch_for_sample_material(client, sample_row, state, material_tier)
                rec.run_id = run_id
                raw_path = save_api_result(raw_dir, run_id, rec.sample_id, material_tier, APIResult(
                    request_url=rec.request_url,
                    request_params=rec.request_params,
                    request_hash=rec.request_hash,
                    http_status=rec.http_status,
                    response_timestamp=rec.response_timestamp,
                    response_json=rec.response_json,
                    error=rec.error,
                ))
                rec.raw_response_path = str(raw_path.relative_to(_PROJECT_ROOT))
                raw_records.append(rec)
                normalized_rows.append(normalize_record(rec))

        raw_records = dedupe_records(raw_records)
        write_raw_records(conn, raw_records)

        normalized_df = pd.DataFrame(normalized_rows)
        if normalized_df.empty:
            normalized_df = pd.DataFrame(columns=NORMALIZED_COLUMNS)
        for col in NORMALIZED_COLUMNS:
            if col not in normalized_df.columns:
                normalized_df[col] = None
        normalized_df = normalized_df[NORMALIZED_COLUMNS]
        write_normalized(conn, normalized_df)

        normalized_df.to_csv(csv_path, index=False)
        normalized_df.to_parquet(parquet_path, index=False)

        expected_rows = len(sample_df) * len(materials)
        missing_required = int((normalized_df["requested_state"].isna() | normalized_df["material_tier_requested"].isna()).sum())
        all_cost_missing_mask = normalized_df[COST_COLUMNS].isna().all(axis=1) if not normalized_df.empty else pd.Series([], dtype=bool)

        checks = {
            "expected_rows": expected_rows,
            "normalized_rows": int(len(normalized_df)),
            "raw_rows": int(len(raw_records)),
            "row_count_match": int(len(normalized_df)) == expected_rows,
            "missing_required_rows": missing_required,
            "rows_missing_all_cost_fields": int(all_cost_missing_mask.sum()) if len(all_cost_missing_mask) else 0,
            "all_rows_missing_all_cost_fields": bool(all_cost_missing_mask.all()) if len(all_cost_missing_mask) else True,
        }

        conn.commit()

        return {
            "run_id": run_id,
            "states": states,
            "materials": materials,
            "sample_locations": int(len(sample_df)),
            "db_path": str(db_path),
            "csv_path": str(csv_path),
            "parquet_path": str(parquet_path),
            "checks": checks,
        }
    finally:
        conn.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RoofVista validation pipeline")
    parser.add_argument("--discover-schema", action="store_true", help="Run schema discovery only")
    parser.add_argument("--states", nargs="+", default=DEFAULT_STATES)
    parser.add_argument("--materials", nargs="+", default=DEFAULT_MATERIALS)
    parser.add_argument("--sample-size", type=int, default=25)
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--sample-config", default=str(DEFAULT_SAMPLE_CONFIG))
    parser.add_argument("--schema-dir", default=str(DEFAULT_SCHEMA_DIR))
    parser.add_argument("--raw-dir", default=str(DEFAULT_RAW_DIR))
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument("--csv-path", default=str(DEFAULT_CSV_PATH))
    parser.add_argument("--parquet-path", default=str(DEFAULT_PARQUET_PATH))
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--backoff-factor", type=float, default=0.8)
    parser.add_argument("--rate-limit-seconds", type=float, default=0.35)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    summary = run_pipeline(args)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
