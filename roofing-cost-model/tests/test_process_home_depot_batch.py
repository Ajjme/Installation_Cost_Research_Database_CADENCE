"""Tests for collision-safe local Home Depot batch orchestration."""

from pathlib import Path

import pytest

from src.process_home_depot_batch import (
    build_batch_paths,
    discover_category_keys,
    process_batch,
)


def test_dated_batch_has_one_combined_output_set(tmp_path):
    paths = build_batch_paths(
        "2026-07-31",
        raw_root=str(tmp_path / "raw"),
        intermediate_dir=str(tmp_path / "intermediate"),
        output_root=str(tmp_path / "output"),
    )

    assert paths.raw_dir == tmp_path / "raw" / "2026-07-31"
    assert paths.normalized_parquet.name == (
        "home_depot_products_normalized_2026-07-31.parquet"
    )
    assert paths.output_dir == tmp_path / "output" / "2026-07-31"


def test_discover_category_keys_uses_filename_prefixes(tmp_path):
    (tmp_path / "asphalt_shingles_27701_p0.html").touch()
    (tmp_path / "metal_shingles_27705_p0.html").touch()

    assert discover_category_keys(str(tmp_path)) == [
        "asphalt_shingles",
        "metal_shingles",
    ]


def test_discover_category_keys_rejects_ambiguous_filename(tmp_path):
    (tmp_path / "metal.html").touch()

    with pytest.raises(ValueError, match="must match"):
        discover_category_keys(str(tmp_path))


def test_build_batch_paths_rejects_directory_traversal():
    with pytest.raises(ValueError, match="Batch name"):
        build_batch_paths("../asphalt")


def test_default_batch_name_must_be_date(tmp_path):
    source_dir = tmp_path / "2026-07-31-asphalt"
    source_dir.mkdir()

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        process_batch(str(source_dir))