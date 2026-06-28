"""Tests for the --save-page capture helper's filename convention."""

from pathlib import Path

from src.retailers.home_depot import (
    _LOCAL_FILENAME_RE,
    _save_page_path,
)


def test_save_page_path_convention():
    p = _save_page_path("local_pages", "asphalt_shingles", "90001", 0)
    assert p == Path("local_pages") / "asphalt_shingles_90001_p0.html"


def test_save_page_path_roundtrips_with_ingestion_regex():
    # A path produced by the saver must be parseable by the ingestion regex.
    p = _save_page_path("local_pages", "metal_roofing", "33101", 2)
    match = _LOCAL_FILENAME_RE.match(p.name)
    assert match is not None
    assert match.group("category") == "metal_roofing"
    assert match.group("zip") == "33101"
    assert match.group("page") == "2"


def test_save_page_path_defaults_zip_when_missing():
    p = _save_page_path("local_pages", "roof_tile", None, 0)
    assert p.name == "roof_tile_00000_p0.html"
    # Still matches the ingestion regex (5-digit placeholder ZIP).
    assert _LOCAL_FILENAME_RE.match(p.name) is not None
