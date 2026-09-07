#!/usr/bin/env python3
"""Tests for lib/raster_reproject.py's reproject_categorical_max -- the shared
rasterio reproject(max) mechanics behind lib/flood_risk.py's JRC/MODIS-flood tiles
and lib/vegetation_mask.py's MODIS Land Cover mosaic (architecture review candidate
"move categorical-raster reprojection out of the Flood Risk module"). Uses its own
minimal synthetic-GeoTIFF fixture rather than either domain's own fixture helper
(test_lib_flood_risk.py's _write_tiny_reclass_tif, test_lib_vegetation_mask.py's
_write_landcover_fixture) -- this module has no flood or vegetation semantics of
its own to borrow.

Regression coverage for a real bug, not a hypothetical: querying
burnable_vegetation_mask() against the actual ~130MB/~3.1-billion-pixel MODIS Land
Cover mosaic (far larger than any fixture here) OOM-killed the process at ~4GB RSS,
because the pre-fix code always read band 1 in full before remapping/reprojecting.
These tests can't reproduce the OOM itself at fixture scale, but they lock the
decimated-read code path's correctness so a future edit can't silently break it
back into an always-full-read function without a test failing.
"""
import numpy as np
import rasterio
from rasterio import Affine

from atmos_gl.lib.raster_reproject import reproject_categorical_max


def _write_fixture(path, values, bounds):
    """A tiny synthetic single-band categorical GeoTIFF (north-up: row 0 = lat_max)."""
    lon_min, lat_min, lon_max, lat_max = bounds
    height, width = values.shape
    transform = Affine(
        (lon_max - lon_min) / width, 0.0, lon_min,
        0.0, -(lat_max - lat_min) / height, lat_max,
    )
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="uint8", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(values, 1)


def _remap_is_category_1(source, _src):
    """A trivial, domain-neutral remap: category 1 -> 1, everything else -> 0."""
    return (source == 1).astype(np.uint8)


def test_decimates_when_over_the_pixel_cap(tmp_path):
    """8x8 source (64px) with a clean north/south split, cap=8 forces a decimated
    read (scale=sqrt(64/8)~=2.83 -> 2x2) -- the classification must still survive
    at that resolution."""
    path = str(tmp_path / "big.tif")
    values = np.zeros((8, 8), dtype=np.uint8)
    values[:4, :] = 1  # north half: category 1
    values[4:, :] = 2  # south half: category 2
    _write_fixture(path, values, bounds=(0.0, 0.0, 8.0, 8.0))

    mask = reproject_categorical_max(
        path,
        dst_lat=[6.0, 2.0],  # north cell, south cell (descending)
        dst_lon=[4.0],
        remap=_remap_is_category_1,
        max_source_pixels=8,
    )

    assert mask.tolist() == [[1], [0]]


def test_full_read_matches_decimated_read(tmp_path):
    """Same source/destination either way -- the decimated path is a memory
    optimisation, not a behaviour change, for a source this uniform."""
    path = str(tmp_path / "big.tif")
    values = np.zeros((8, 8), dtype=np.uint8)
    values[:4, :] = 1
    values[4:, :] = 2
    _write_fixture(path, values, bounds=(0.0, 0.0, 8.0, 8.0))

    full = reproject_categorical_max(
        path, dst_lat=[6.0, 2.0], dst_lon=[4.0], remap=_remap_is_category_1
    )
    decimated = reproject_categorical_max(
        path,
        dst_lat=[6.0, 2.0],
        dst_lon=[4.0],
        remap=_remap_is_category_1,
        max_source_pixels=8,
    )

    assert full.tolist() == decimated.tolist()


def test_skips_decimation_under_the_pixel_cap(tmp_path):
    """A cap the source already satisfies must behave exactly like omitting it --
    confirms max_source_pixels is opt-in and doesn't change small-tile callers."""
    path = str(tmp_path / "small.tif")
    values = np.array([[1, 3], [4, 1]], dtype=np.uint8)
    _write_fixture(path, values, bounds=(0.0, 0.0, 20.0, 20.0))

    without_cap = reproject_categorical_max(
        path, dst_lat=[15.0, 5.0], dst_lon=[5.0, 15.0], remap=_remap_is_category_1
    )
    with_cap = reproject_categorical_max(
        path,
        dst_lat=[15.0, 5.0],
        dst_lon=[5.0, 15.0],
        remap=_remap_is_category_1,
        max_source_pixels=1_000_000,
    )

    assert without_cap.tolist() == with_cap.tolist()
