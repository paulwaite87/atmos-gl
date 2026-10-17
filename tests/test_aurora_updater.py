#!/usr/bin/env python3
"""AuroraUpdater (tasks/aurora.py): the cached OVATION grid -> the raw data texture the
GPU fill layer reads (palette, cutoff and opacity are applied in the browser, so none of
them is baked in here), plus the sidecar carrying the grid's observation/forecast times.
Asserted on the PNG that lands on disk, decoded with the same 16-bit R*256+G convention
as lib/texture.py's encode_frames. Prior art: test_population_density_collector.py's
updater tests."""

import json
import os
import time

import numpy as np
from PIL import Image

from terrascope.lib.aurora import aurora_grid_cache_path, save_aurora_grid
from terrascope.tasks.aurora import AuroraUpdater


def make_updater(workdir):
    u = AuroraUpdater.__new__(AuroraUpdater)
    u.workdir = str(workdir)
    u.section = "aurora"
    u.output_path = str(workdir / "data" / "aurora.png")
    return u


def cache_grid(workdir, cells, observed="2026-10-17T05:53:00Z", forecast="2026-10-17T06:54:00Z"):
    """Writes the collector's cache file: (lat -90..90, lon 0..359) with the given
    {(lon, lat): probability} cells set."""
    probability = np.zeros((181, 360), dtype=np.uint8)
    for (lon, lat), value in cells.items():
        probability[lat + 90, lon] = value
    save_aurora_grid(aurora_grid_cache_path(str(workdir)), probability, observed, forecast)


def decoded(path):
    """The texture as probability 0..100 (float), rows north first, columns lon -180..179."""
    px = np.asarray(Image.open(path)).astype(np.float64)
    return (px[..., 0] * 256 + px[..., 1]) / 65535 * 100


def test_the_texture_is_north_first_and_runs_from_minus_180_east(tmp_path):
    cache_grid(
        tmp_path,
        {(10, 65): 50, (350, -70): 20, (180, 0): 30, (179, 0): 40, (0, -90): 10},
    )
    u = make_updater(tmp_path)

    u.run()

    tex = decoded(u.output_path)
    assert tex.shape == (181, 360)
    assert round(tex[90 - 65, 180 + 10]) == 50  # 65N 10E: row 25, column 190
    assert round(tex[90 + 70, 180 - 10]) == 20       # 70S 10W (feed lon 350): column 170
    assert round(tex[90, 0]) == 30                    # 180 is the west edge...
    assert round(tex[90, 359]) == 40                  # ...and 179E the east edge
    assert round(tex[180, 180]) == 10                 # 90S, lon 0: last row, centre
    assert round(tex.sum()) == 50 + 20 + 30 + 40 + 10  # nothing else was set


def test_the_times_the_grid_carries_are_written_beside_the_texture(tmp_path):
    cache_grid(tmp_path, {}, observed="2026-10-17T05:53:00Z", forecast="2026-10-17T06:54:00Z")
    u = make_updater(tmp_path)

    u.run()

    with open(tmp_path / "data" / "aurora_meta.json") as f:
        meta = json.load(f)
    assert meta["aurora"] == {
        "observed": "2026-10-17T05:53:00Z",
        "forecast": "2026-10-17T06:54:00Z",
    }


def test_the_texture_is_rerendered_only_when_the_grid_is_newer(tmp_path):
    cache_grid(tmp_path, {(10, 65): 50})
    u = make_updater(tmp_path)
    u.run()
    past = time.time() - 3600
    os.utime(u.output_path, (past, past))  # pretend the render is an hour old
    grid = aurora_grid_cache_path(str(tmp_path))
    os.utime(grid, (past - 60, past - 60))  # ...and the grid older than it

    u.run()
    assert os.path.getmtime(u.output_path) == past  # nothing newer: left alone

    cache_grid(tmp_path, {(10, 65): 90}, observed="2026-10-17T06:03:00Z")
    u.run()
    assert os.path.getmtime(u.output_path) > past
    assert round(decoded(u.output_path)[90 - 65, 180 + 10]) == 90
