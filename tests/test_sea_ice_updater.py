#!/usr/bin/env python3
"""SeaIceUpdater (tasks/sea_ice.py): the cached OISST ice netCDF -> the raw data texture
the GPU fill layer reads (colour ramp, cutoff and opacity are applied in the browser),
plus the sidecar carrying the day shown. The netCDF is a small synthetic one in the real
file's layout (lat south first, lon 0..360 cell centres, one field per day, fraction 0..1,
NaN where there is no ice). Asserted on the PNG that lands on disk, decoded with the
16-bit R*256+G convention of lib/texture.py's encode_frames. Prior art:
test_aurora_updater.py, test_sst_texture_plot.py."""

import json
import os
import time

import numpy as np
import pytest
import xarray as xr
from PIL import Image

from terrascope.lib.sea_ice import sea_ice_cache_path
from terrascope.tasks.sea_ice import SeaIceUpdater

LATS = np.array([-67.5, -22.5, 22.5, 67.5])                      # south first, as OISST
LONS = np.array([22.5, 67.5, 112.5, 157.5, 202.5, 247.5, 292.5, 337.5])  # 0..360


def make_updater(workdir):
    u = SeaIceUpdater.__new__(SeaIceUpdater)
    u.workdir = str(workdir)
    u.section = "sea_ice"
    u.output_path = str(workdir / "data" / "sea_ice.png")
    return u


def cache_ice(workdir, days):
    """Writes the collector's cache file: `days` is a list of (iso date, {(lat, lon):
    fraction}); every other cell is NaN (no ice)."""
    fields = []
    for _date, cells in days:
        field = np.full((len(LATS), len(LONS)), np.nan, dtype=np.float32)
        for (lat, lon), value in cells.items():
            field[list(LATS).index(lat), list(LONS).index(lon)] = value
        fields.append(field)
    ds = xr.Dataset(
        {"icec": (("time", "lat", "lon"), np.stack(fields))},
        coords={"time": np.array([d for d, _ in days], dtype="datetime64[ns]"), "lat": LATS, "lon": LONS},
    )
    path = sea_ice_cache_path(str(workdir))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ds.to_netcdf(path)


def decoded(path):
    """The texture as concentration 0..100 percent (float) and its alpha channel."""
    px = np.asarray(Image.open(path)).astype(np.float64)
    return (px[..., 0] * 256 + px[..., 1]) / 65535 * 100, px[..., 3]


def test_the_latest_day_is_drawn_north_first_from_minus_180_on_a_fixed_percent_scale(tmp_path):
    cache_ice(
        tmp_path,
        [
            ("2026-10-14", {(67.5, 22.5): 0.20}),  # yesterday: must not appear
            (
                "2026-10-15",
                {(67.5, 22.5): 0.5, (-67.5, 337.5): 0.9, (22.5, 202.5): 1.0},
            ),
        ],
    )
    u = make_updater(tmp_path)

    u.run()

    tex, alpha = decoded(u.output_path)
    assert tex.shape == (4, 8)
    assert round(tex[0, 4]) == 50   # 67.5N 22.5E: top row, just east of the centre
    assert round(tex[3, 3]) == 90   # 67.5S 22.5W (feed lon 337.5): bottom row, just west
    assert round(tex[1, 0]) == 100  # 22.5N 157.5W (feed lon 202.5): far-west column
    assert (alpha > 0).sum() == 3   # every ice-free cell is transparent (NaN)


def test_the_day_shown_is_written_beside_the_texture(tmp_path):
    cache_ice(tmp_path, [("2026-10-14", {}), ("2026-10-15", {(67.5, 22.5): 0.5})])
    u = make_updater(tmp_path)

    u.run()

    with open(tmp_path / "data" / "sea_ice_meta.json") as f:
        meta = json.load(f)
    assert meta["sea_ice"] == {"date": "2026-10-15"}


def test_nothing_is_rendered_before_the_file_exists(tmp_path):
    u = make_updater(tmp_path)

    u.run()

    assert not os.path.exists(u.output_path)


def test_the_texture_is_rerendered_only_when_the_file_is_newer(tmp_path):
    cache_ice(tmp_path, [("2026-10-15", {(67.5, 22.5): 0.5})])
    u = make_updater(tmp_path)
    u.run()
    past = time.time() - 3600
    os.utime(u.output_path, (past, past))  # pretend the render is an hour old
    os.utime(sea_ice_cache_path(str(tmp_path)), (past - 60, past - 60))  # ...file older

    u.run()
    assert os.path.getmtime(u.output_path) == past  # nothing newer: left alone

    cache_ice(tmp_path, [("2026-10-16", {(67.5, 22.5): 0.9})])
    u.run()
    assert os.path.getmtime(u.output_path) > past
    assert round(decoded(u.output_path)[0][0, 4]) == 90
    with open(tmp_path / "data" / "sea_ice_meta.json") as f:
        assert json.load(f)["sea_ice"] == {"date": "2026-10-16"}
