"""Tests for src.normalize_products."""

import math

import pandas as pd
import pytest

from src.normalize_products import (
    SQFT_PER_SQUARE,
    bulk_discount_pct,
    normalize,
    parse_coverage_sqft,
    parse_price,
    price_per_sqft,
    price_per_square,
)


# --------------------------------------------------------------------------- #
# Price parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [
        ("$41.47", 41.47),
        ("41.47", 41.47),
        ("$1,234.50", 1234.50),
        (37.32, 37.32),
        (40, 40.0),
        ("USD 99.99 each", 99.99),
        ("", None),
        (None, None),
        ("no price", None),
    ],
)
def test_parse_price(value, expected):
    result = parse_price(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_price_ignores_bool():
    # bool is a subclass of int but should not be treated as a price.
    assert parse_price(True) is None


# --------------------------------------------------------------------------- #
# Coverage parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "value,expected",
    [
        ("33.33 sq. ft. per bundle", 33.33),
        ("33.33 sq ft per bundle", 33.33),
        ("Covers 100 sq. ft.", 100.0),
        ("98.4 square feet", 98.4),
        (33.33, 33.33),
        ("", None),
        (None, None),
        ("no coverage info", None),
    ],
)
def test_parse_coverage_sqft(value, expected):
    result = parse_coverage_sqft(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


# --------------------------------------------------------------------------- #
# Derived price metrics
# --------------------------------------------------------------------------- #
def test_price_per_sqft():
    assert price_per_sqft(41.47, 33.33) == pytest.approx(1.244, abs=1e-3)


def test_price_per_square():
    # 41.47 / 33.33 * 100 ~= 124.42
    assert price_per_square(41.47, 33.33) == pytest.approx(124.42, abs=1e-2)
    assert price_per_square(41.47, 33.33) == pytest.approx(
        price_per_sqft(41.47, 33.33) * SQFT_PER_SQUARE
    )


def test_price_per_square_missing_inputs():
    assert price_per_square(None, 33.33) is None
    assert price_per_square(41.47, None) is None
    assert price_per_square(41.47, 0) is None


# --------------------------------------------------------------------------- #
# Bulk discount
# --------------------------------------------------------------------------- #
def test_bulk_discount_pct():
    # 37.32 / 41.47 -> ~10% discount
    assert bulk_discount_pct(41.47, 37.32) == pytest.approx(0.1001, abs=1e-3)


def test_bulk_discount_pct_missing():
    assert bulk_discount_pct(None, 37.32) is None
    assert bulk_discount_pct(41.47, None) is None
    assert bulk_discount_pct(0, 37.32) is None


# --------------------------------------------------------------------------- #
# normalize() end to end
# --------------------------------------------------------------------------- #
def _geo():
    return pd.DataFrame(
        [
            {
                "zip_code": "90001",
                "city": "Los Angeles",
                "state": "CA",
                "cbsa_name": "Los Angeles-Long Beach-Anaheim, CA",
                "cbsa_code": "31080",
            }
        ]
    )


def test_normalize_basic_row():
    raw = pd.DataFrame(
        [
            {
                "retailer": "Home Depot",
                "scrape_date": "2026-06-27",
                "zip_code": "90001",
                "product_id": "100",
                "product_name": "GAF Timberline HDZ Architectural Shingles 33.33 sq. ft. per bundle",
                "brand": "GAF",
                "retail_price_per_unit": "$41.47",
                "bulk_price_per_unit": "$37.32",
                "bulk_threshold": "39",
                "coverage_sqft_per_unit": "33.33 sq. ft. per bundle",
                "availability_status": "in stock",
                "source_method": "requests",
            }
        ]
    )
    out = normalize(raw, _geo())
    assert len(out) == 1
    row = out.iloc[0]
    assert row["material_class"] == "asphalt_architectural"
    assert row["retail_price_per_unit"] == pytest.approx(41.47)
    assert row["coverage_sqft_per_unit"] == pytest.approx(33.33)
    assert row["price_per_square"] == pytest.approx(124.42, abs=1e-2)
    assert row["bulk_price_per_square"] == pytest.approx(111.97, abs=1e-2)
    assert row["bulk_discount_pct"] == pytest.approx(0.1001, abs=1e-3)
    assert row["city"] == "Los Angeles"
    assert row["cbsa_code"] == "31080"
    assert row["coverage_flag"] == "ok"


def test_normalize_coverage_from_product_name_fallback():
    raw = pd.DataFrame(
        [
            {
                "zip_code": "90001",
                "product_id": "101",
                "product_name": "Architectural Shingle covers 98.4 sq. ft.",
                "retail_price_per_unit": "100",
            }
        ]
    )
    out = normalize(raw, _geo())
    assert out.iloc[0]["coverage_sqft_per_unit"] == pytest.approx(98.4)


def test_normalize_missing_coverage_flagged_and_no_imputation():
    raw = pd.DataFrame(
        [
            {
                "zip_code": "90001",
                "product_id": "102",
                "product_name": "Mystery Shingle",
                "retail_price_per_unit": "50",
            }
        ]
    )
    out = normalize(raw, _geo())
    row = out.iloc[0]
    assert row["coverage_sqft_per_unit"] is None or (
        isinstance(row["coverage_sqft_per_unit"], float)
        and math.isnan(row["coverage_sqft_per_unit"])
    )
    assert row["price_per_square"] is None or (
        isinstance(row["price_per_square"], float) and math.isnan(row["price_per_square"])
    )
    assert row["coverage_flag"] == "missing"


def test_normalize_suspicious_coverage_flag():
    raw = pd.DataFrame(
        [
            {
                "zip_code": "90001",
                "product_id": "103",
                "product_name": "Weird product 99999 sq ft",
                "retail_price_per_unit": "50",
                "coverage_sqft_per_unit": 99999.0,
            }
        ]
    )
    out = normalize(raw, _geo())
    assert out.iloc[0]["coverage_flag"] == "suspicious"


def test_normalize_empty_frame():
    out = normalize(pd.DataFrame(), _geo())
    assert out.empty


def test_normalize_malformed_rows_do_not_crash():
    raw = pd.DataFrame(
        [
            {"zip_code": "90001"},  # almost everything missing
            {"product_name": None, "retail_price_per_unit": "garbage"},
        ]
    )
    out = normalize(raw, _geo())
    assert len(out) == 2
    # No price could be parsed for either -> derived metrics empty.
    assert out["material_class"].notna().all()


def test_normalize_without_geo():
    raw = pd.DataFrame(
        [{"zip_code": "00000", "product_id": "1", "product_name": "3-tab shingle"}]
    )
    out = normalize(raw)  # no geo provided
    assert out.iloc[0]["material_class"] == "asphalt_3_tab"
    assert pd.isna(out.iloc[0]["city"])
