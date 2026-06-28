"""Tests for RoofVista validation pipeline."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from src.fetch_roofvista_validation import (
    RequestRecord,
    build_full_url,
    create_tables,
    dedupe_records,
    normalize_record,
)


def test_build_full_url_includes_query_params() -> None:
    url = build_full_url(
        "https://roofvista.com/api/v1/public/pricing",
        {"state": "CT", "material": "architectural", "zip": "06103"},
    )
    assert "state=CT" in url
    assert "material=architectural" in url
    assert "zip=06103" in url


def test_normalize_record_parses_cost_fields() -> None:
    record = RequestRecord(
        run_id="run1",
        request_hash="h1",
        sample_id="S1",
        requested_state="CT",
        requested_city="Hartford",
        requested_zip_code="06103",
        requested_address=None,
        latitude=41.76,
        longitude=-72.67,
        material_tier_requested="architectural shingles",
        requested_material_param="architectural",
        geographic_resolution="zip",
        request_url="https://roofvista.com/api/v1/public/pricing?state=CT&material=architectural",
        request_params={"state": "CT", "material": "architectural"},
        http_status=200,
        response_timestamp="2026-06-27T00:00:00+00:00",
        raw_response_path="data_raw/roofvista/responses/a.json",
        response_json={
            "estimate": {
                "material": "architectural shingles",
                "estimated_cost_per_square": 525,
                "estimated_cost_per_sqft": 5.25,
                "installed_cost_per_square": 600,
                "material_cost_per_square": 360,
                "labor_cost_per_square": 240,
                "currency": "USD",
                "effective_date": "2026-06-01",
            }
        },
        error=None,
    )

    row = normalize_record(record)
    assert row["parse_status"] == "ok"
    assert row["estimated_cost_per_square"] == 525.0
    assert row["installed_cost_per_square"] == 600.0
    assert row["material_cost_per_square"] == 360.0


def test_normalize_record_parses_roofvista_price_shape() -> None:
    record = RequestRecord(
        run_id="run1",
        request_hash="h1b",
        sample_id="S1",
        requested_state="MA",
        requested_city="Boston",
        requested_zip_code="02108",
        requested_address=None,
        latitude=42.35,
        longitude=-71.06,
        material_tier_requested="architectural shingles",
        requested_material_param="architectural",
        geographic_resolution="zip",
        request_url="https://roofvista.com/api/v1/public/pricing?state=MA&material=architectural",
        request_params={"state": "MA", "material": "architectural"},
        http_status=200,
        response_timestamp="2026-06-27T00:00:00+00:00",
        raw_response_path="data_raw/roofvista/responses/a2.json",
        response_json={
            "state": "MA",
            "state_name": "Massachusetts",
            "material": "architectural",
            "price_per_square": 800,
            "price_per_sqft": 8,
            "labor_cost_percent": 50,
            "material_cost_percent": 50,
            "unit": "USD per roofing square (100 sq ft)",
            "attribution": "Data by RoofVista",
        },
        error=None,
    )

    row = normalize_record(record)
    assert row["parse_status"] == "ok"
    assert row["estimated_cost_per_square"] == 800.0
    assert row["estimated_cost_per_sqft"] == 8.0
    assert row["material_share"] == 0.5
    assert row["labor_share"] == 0.5
    assert row["material_cost_per_square"] == 400.0
    assert row["labor_cost_per_square"] == 400.0


def test_create_tables_creates_sqlite_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "roofvista.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        create_tables(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    finally:
        conn.close()

    assert "roofvista_runs" in tables
    assert "roofvista_sample_locations" in tables
    assert "roofvista_raw_responses" in tables
    assert "roofvista_normalized_estimates" in tables


def test_pipeline_resilient_on_failed_request() -> None:
    failed = RequestRecord(
        run_id="run2",
        request_hash="h2",
        sample_id="S2",
        requested_state="MA",
        requested_city="Boston",
        requested_zip_code="02108",
        requested_address=None,
        latitude=42.35,
        longitude=-71.06,
        material_tier_requested="premium shingles",
        requested_material_param="premium",
        geographic_resolution="state",
        request_url="https://roofvista.com/api/v1/public/pricing?state=MA&material=premium",
        request_params={"state": "MA", "material": "premium"},
        http_status=500,
        response_timestamp="2026-06-27T00:00:00+00:00",
        raw_response_path="data_raw/roofvista/responses/b.json",
        response_json={"error": "server"},
        error="HTTP 500",
    )

    row = normalize_record(failed)
    assert row["parse_status"] in {"request_failed", "http_error"}
    assert row["material_tier_requested"] == "premium shingles"


def test_state_only_response_flagged_and_deduped() -> None:
    rec1 = RequestRecord(
        run_id="run3",
        request_hash="samehash",
        sample_id="MA-0001",
        requested_state="MA",
        requested_city="Boston",
        requested_zip_code="02108",
        requested_address=None,
        latitude=42.35,
        longitude=-71.06,
        material_tier_requested="tile",
        requested_material_param="tile",
        geographic_resolution="state",
        request_url="https://roofvista.com/api/v1/public/pricing?state=MA&material=tile",
        request_params={"state": "MA", "material": "tile"},
        http_status=200,
        response_timestamp="2026-06-27T00:00:00+00:00",
        raw_response_path="data_raw/roofvista/responses/c.json",
        response_json={"estimated_cost_per_square": 700},
        error=None,
    )
    rec2 = RequestRecord(
        run_id="run3",
        request_hash="samehash",
        sample_id="MA-0002",
        requested_state="MA",
        requested_city="Cambridge",
        requested_zip_code="02139",
        requested_address=None,
        latitude=42.36,
        longitude=-71.10,
        material_tier_requested="tile",
        requested_material_param="tile",
        geographic_resolution="state",
        request_url="https://roofvista.com/api/v1/public/pricing?state=MA&material=tile",
        request_params={"state": "MA", "material": "tile"},
        http_status=200,
        response_timestamp="2026-06-27T00:00:00+00:00",
        raw_response_path="data_raw/roofvista/responses/d.json",
        response_json={"estimated_cost_per_square": 700},
        error=None,
    )

    deduped = dedupe_records([rec1, rec2])
    assert len(deduped) == 1

    row = normalize_record(rec1)
    assert row["geographic_resolution"] == "state"
    assert row["estimated_cost_per_square"] == 700.0
