#!/usr/bin/env python3
"""Tests for the pure, previously-untested functions in tiles/raster_tiles.py
(architecture review candidate "give raster_tiles the seam/tests other routers have").
tile_pixel_lonlat/build_lut/sample_field/compose_tile_rgba/current_version are
explicitly documented in the module as "pure; testable without cartopy" -- designed
for exactly this, never actually exercised. compose_tile_rgba's land_fn parameter
exists specifically so a test can inject a fake land mask instead of pulling in
cartopy/shapely.
"""
import numpy as np
import pytest

from worldmap.tiles.raster_tiles import (
    tile_pixel_lonlat,
    build_lut,
    sample_field,
    compose_tile_rgba,
    current_version,
    TileSpec,
)


class FakeConfig:
    def __init__(self, sections=None):
        self._sections = sections or {}

    def get_section(self, section):
        return self._sections.get(section, {})

    def get_setting(self, section, setting, default=None):
        return self._sections.get(section, {}).get(setting, default)


# ---- tile_pixel_lonlat ------------------------------------------------------

def test_tile_pixel_lonlat_z0_spans_the_whole_world():
    lon, lat = tile_pixel_lonlat(0, 0, 0, px=4)
    assert lon[0] == pytest.approx(-180 + 45, abs=0.01)  # first pixel centre
    assert lon[-1] == pytest.approx(180 - 45, abs=0.01)
    # Web Mercator: row 0 (top) is the northernmost pixel.
    assert lat[0] > lat[-1]


def test_tile_pixel_lonlat_top_row_near_north_limit():
    _, lat = tile_pixel_lonlat(0, 0, 0, px=256)
    assert lat[0] < 85.06  # inside the Web Mercator latitude limit
    assert lat[0] > 84.0


def test_tile_pixel_lonlat_deeper_zoom_narrows_the_lon_span():
    lon_z0, _ = tile_pixel_lonlat(0, 0, 0, px=4)
    lon_z2, _ = tile_pixel_lonlat(2, 0, 0, px=4)
    span_z0 = lon_z0[-1] - lon_z0[0]
    span_z2 = lon_z2[-1] - lon_z2[0]
    assert span_z2 == pytest.approx(span_z0 / 4, rel=0.05)


# ---- build_lut ---------------------------------------------------------------

def test_build_lut_returns_256_rgb_entries():
    spec = TileSpec(section="test_lut_shape", cmap_name="viridis")
    lut = build_lut(spec, "viridis")
    assert lut.shape == (256, 3)
    assert lut.dtype == np.uint8


def test_build_lut_uses_palette_registry_endpoints():
    spec = TileSpec(
        section="test_lut_palette",
        palettes={"p1": [(1.0, 0.0, 0.0), (0.0, 0.0, 1.0)]},  # red -> blue
    )
    lut = build_lut(spec, "p1")
    assert tuple(lut[0]) == (255, 0, 0)
    assert tuple(lut[-1]) == (0, 0, 255)


def test_build_lut_caches_by_section_and_palette_id():
    spec = TileSpec(
        section="test_lut_cache", palettes={"a": [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]}
    )
    first = build_lut(spec, "a")
    second = build_lut(spec, "a")
    assert first is second  # same cached array object, not just equal


# ---- sample_field --------------------------------------------------------------

def test_sample_field_nearest_grid_value():
    field = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=np.float32)
    meta = {"lat0": 0.0, "dlat": 1.0, "lon0": 0.0, "dlon": 1.0, "nlat": 2, "nlon": 2}
    result = sample_field(field, meta, np.array([[0.0]]), np.array([[0.0]]))
    assert result[0] == pytest.approx(1.0)


def test_sample_field_wraps_longitude():
    field = np.array([[10.0, 20.0, 30.0]], dtype=np.float32)
    meta = {"lat0": 0.0, "dlat": 1.0, "lon0": 0.0, "dlon": 1.0, "nlat": 1, "nlon": 3}
    # lon=3 wraps to col 0 (mod nlon=3)
    result = sample_field(field, meta, np.array([[3.0]]), np.array([[0.0]]))
    assert result[0] == pytest.approx(10.0)


# ---- compose_tile_rgba -------------------------------------------------------

def _uniform_field(value, shape=(4, 4)):
    field = np.full(shape, value, dtype=np.float32)
    meta = {"lat0": -80.0, "dlat": 40.0, "lon0": -180.0, "dlon": 90.0,
            "nlat": shape[0], "nlon": shape[1]}
    return field, meta


def test_compose_tile_rgba_shape_and_alpha_channel():
    spec = TileSpec(section="test_compose", vmin=0.0, vmax=10.0)
    field, meta = _uniform_field(5.0)
    lut = np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 3))
    rgba = compose_tile_rgba(spec, field, meta, lut, alpha255=200, threshold=None,
                              z=0, x=0, y=0, px=8)
    assert rgba.shape == (8, 8, 4)
    assert rgba.dtype == np.uint8
    assert (rgba[..., 3] == 200).all()  # uniform alpha, no threshold


def test_compose_tile_rgba_threshold_zeroes_alpha_below_cutoff():
    spec = TileSpec(section="test_compose_thresh", vmin=0.0, vmax=10.0)
    field, meta = _uniform_field(2.0)  # below the threshold
    lut = np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 3))
    rgba = compose_tile_rgba(spec, field, meta, lut, alpha255=200, threshold=5.0,
                              z=0, x=0, y=0, px=4)
    assert (rgba[..., 3] == 0).all()


def test_compose_tile_rgba_land_mask_via_injected_land_fn():
    """land_fn exists specifically so this can be tested without cartopy/shapely."""
    spec = TileSpec(section="test_compose_land", vmin=0.0, vmax=10.0, mask_land=True)
    field, meta = _uniform_field(5.0)
    lut = np.tile(np.arange(256, dtype=np.uint8)[:, None], (1, 3))
    all_land = lambda lon2d, lat2d: np.ones(lon2d.shape, dtype=bool)  # noqa: E731

    rgba = compose_tile_rgba(spec, field, meta, lut, alpha255=200, threshold=None,
                              z=0, x=0, y=0, px=4, land_fn=all_land)

    assert (rgba[..., 3] == 0).all()  # entirely masked as land


# ---- current_version ---------------------------------------------------------

def test_current_version_is_a_12char_hex_id():
    spec = TileSpec(section="test_version")
    config = FakeConfig({"test_version": {}})
    version = current_version(spec, config, "2026-01-01", "00", 3)
    assert len(version) == 12
    int(version, 16)  # raises if not valid hex


def test_current_version_changes_with_forecast_hour():
    spec = TileSpec(section="test_version_fh")
    config = FakeConfig({"test_version_fh": {}})
    v1 = current_version(spec, config, "2026-01-01", "00", 3)
    v2 = current_version(spec, config, "2026-01-01", "00", 4)
    assert v1 != v2


def test_current_version_stable_for_identical_inputs():
    spec = TileSpec(section="test_version_stable")
    config = FakeConfig({"test_version_stable": {}})
    v1 = current_version(spec, config, "2026-01-01", "00", 3)
    v2 = current_version(spec, config, "2026-01-01", "00", 3)
    assert v1 == v2


def test_current_version_changes_when_palette_setting_changes():
    spec = TileSpec(
        section="test_version_palette",
        palettes={"a": [(0, 0, 0)], "b": [(1, 1, 1)]},
        default_palette="a",
        palette_setting="palette",
    )
    v_a = current_version(
        spec, FakeConfig({"test_version_palette": {"palette": "a"}}), "2026-01-01", "00", 3
    )
    v_b = current_version(
        spec, FakeConfig({"test_version_palette": {"palette": "b"}}), "2026-01-01", "00", 3
    )
    assert v_a != v_b
