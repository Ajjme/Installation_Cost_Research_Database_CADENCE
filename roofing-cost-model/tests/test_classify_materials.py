"""Tests for src.classify_materials."""

import pytest

from src.classify_materials import (
    MATERIAL_CLASSES,
    classify_product,
    classify_text,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Owens Corning Supreme 3-Tab Shingles", "asphalt_3_tab"),
        ("Generic Three Tab Strip Shingle", "asphalt_3_tab"),
        ("GAF Timberline HDZ Architectural Shingles", "asphalt_architectural"),
        ("Owens Corning Oakridge Laminated Shingle", "asphalt_architectural"),
        ("Certainteed Dimensional Shingle", "asphalt_architectural"),
        ("GAF Grand Manor Designer Premium Shingle", "asphalt_premium_architectural"),
        ("Class 4 Impact Resistant Shingle", "asphalt_premium_architectural"),
        ("Luxury Presidential Shake Shingle", "asphalt_premium_architectural"),
        ("Standing Seam Metal Roof Panel", "metal_standing_seam"),
        ("Corrugated Galvalume Steel Metal Panel", "metal_corrugated_panel"),
        ("Ribbed Aluminum Metal Roofing Panel", "metal_corrugated_panel"),
        ("Spanish Clay Roof Tile", "tile_clay"),
        ("Terra Cotta Barrel Tile", "tile_clay"),
        ("Flat Concrete Roof Tile", "tile_concrete"),
        ("Generic Slate Tile", "tile_unspecified"),
        ("Garden Mulch Bag", "unclassified"),
    ],
)
def test_classify_text(text, expected):
    assert classify_text(text) == expected


def test_premium_beats_architectural():
    # Contains both "architectural" and a premium term -> premium wins.
    assert (
        classify_text("Premium Architectural Designer Shingle")
        == "asphalt_premium_architectural"
    )


def test_three_tab_beats_architectural():
    # 3-tab is the most specific asphalt rule and is checked first.
    assert classify_text("3-Tab Architectural Shingle") == "asphalt_3_tab"


def test_corrugated_without_metal_is_not_metal_panel():
    # "corrugated" without a metal word should not classify as metal panel.
    assert classify_text("Corrugated Plastic Panel") == "unclassified"


def test_classify_product_uses_multiple_fields():
    row = {
        "product_name": "Mystery Roofing Product",
        "brand": "GAF",
        "material": "asphalt",
        "product_type": "Architectural Shingle",
        "breadcrumb": "Roofing > Shingles",
    }
    assert classify_product(row) == "asphalt_architectural"


def test_classify_product_handles_missing_fields():
    assert classify_product({}) == "unclassified"


def test_classify_product_handles_none():
    assert classify_product(None) == "unclassified"


def test_classify_product_tolerates_non_string_values():
    row = {"product_name": 12345, "review_count": None, "rating": 4.5}
    # Should not raise; numeric-only text is unclassified.
    assert classify_product(row) == "unclassified"


def test_all_results_in_controlled_vocabulary():
    samples = [
        "3-tab shingle",
        "architectural shingle",
        "premium designer shingle",
        "standing seam metal",
        "corrugated steel metal panel",
        "clay tile",
        "concrete tile",
        "slate tile",
        "random product",
    ]
    for s in samples:
        assert classify_text(s) in MATERIAL_CLASSES


def test_empty_text_is_unclassified():
    assert classify_text("") == "unclassified"
    assert classify_text(None) == "unclassified"
