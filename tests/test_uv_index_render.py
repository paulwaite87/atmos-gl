#!/usr/bin/env python3
"""The UV Index spec in the shared scalar-field renderer (tasks/scalar_field.py): WHO-band
contour levels, transparency below UVI 1, and the data texture scaled to the layer's
range. The UVI values themselves are computed (and tested) in lib/uv_index.py."""

from unittest.mock import MagicMock, patch

import numpy as np

from terrascope.tasks.common import ForecastState
from terrascope.tasks.scalar_field import SPECS, ScalarFieldUpdater


def make_updater():
    spec = SPECS["uv_index"]
    u = ScalarFieldUpdater.__new__(ScalarFieldUpdater)
    u.spec = spec
    u.section = spec.product
    u.status_product = spec.product
    u.settings = {}
    u.common = {}
    u.output_path = "/tmp/out/uv_index.png"
    u.map_data = MagicMock()
    u.map_data.region.region_identifier = "global"
    u.regrid_for_lod = MagicMock(return_value=([0], [0], [[0]]))
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/uv_index_f003.png")
    return u


def render():
    u = make_updater()
    field = {"lat": [0], "lon": [0], "values": [[5.0]]}
    state = ForecastState.at_hour("2026-06-13", "18", 3)
    with (
        patch("terrascope.tasks.scalar_field.Plot") as plot,
        patch("terrascope.tasks.scalar_field.encode_frames") as encode,
    ):
        u.plot(field, state)
    return plot.return_value.ax.contourf, encode


def test_the_spec_is_the_who_scale_with_one_to_fourteen_as_its_visible_range():
    spec = SPECS["uv_index"]
    assert (spec.title, spec.vmin, spec.vmax) == ("UV Index", 0.0, 14.0)
    assert [(lo, name) for lo, name, _ in spec.bands] == [
        (0.0, "Low"),
        (3.0, "Moderate"),
        (6.0, "High"),
        (8.0, "Very high"),
        (11.0, "Extreme"),
    ]


def test_contours_are_drawn_at_the_band_edges_starting_at_one():
    contourf, _ = render()
    assert list(contourf.call_args.kwargs["levels"]) == [1.0, 3.0, 6.0, 8.0, 11.0, 14.0]
    assert contourf.call_args.kwargs["extend"] == "max"


def test_each_band_is_filled_with_its_who_colour_and_overflow_is_extreme():
    contourf, _ = render()
    cmap = contourf.call_args.kwargs["cmap"]
    colours = [cmap(i)[:3] for i in range(5)]
    from matplotlib.colors import to_rgb

    assert colours == [
        to_rgb(c) for c in ("#4eb400", "#f7e400", "#f85900", "#d8001d", "#6b49c8")
    ]
    assert cmap(cmap.N + 1)[:3] == to_rgb("#6b49c8")


def test_the_data_texture_is_scaled_to_the_layers_range():
    _, encode = render()
    assert encode.call_args.args[2:] == (0.0, 14.0)
