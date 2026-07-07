#!/usr/bin/env python3
"""Tests for the spec-driven scalar-field renderer (architecture review candidate "One
scalar-field renderer, many specs"). temperature/ozone/stormwatch had byte-identical
plot()/run() bodies differing only in a small spec; they now share ScalarFieldUpdater,
parametrised by ScalarFieldSpec. These tests lock the spec->render wiring so a fourth
field (one SPECS entry) can't silently mis-map cmap/range/extend/ticks/title, and guard
the partial() binding in layer_builder.
"""
from functools import partial
from unittest.mock import patch, MagicMock

import pytest

from worldmap.tasks.common import ForecastState
from worldmap.tasks.scalar_field import ScalarFieldUpdater, ScalarFieldSpec, SPECS


def make_bare_updater(spec):
    """Bypass Updater.__init__ (does config/IO) and wire only what plot() reads."""
    u = ScalarFieldUpdater.__new__(ScalarFieldUpdater)
    u.spec = spec
    u.section = spec.product
    u.status_product = spec.product
    u.settings = {}
    u.map_region_bbox = (-180, -90, 180, 90)
    u.output_path = "/tmp/out/layer.png"
    u.map_data = MagicMock()
    u.map_data.region.region_identifier = "global"
    # Methods that touch IO / heavy libs are exercised elsewhere; stub them here.
    u.regrid_for_lod = MagicMock(return_value=([0], [0], [[0]]))
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/layer_f003.png")
    u.save_key_image = MagicMock()
    return u


@pytest.mark.parametrize("key", ["temperature", "ozone", "stormwatch"])
def test_plot_dispatches_spec_to_render(key):
    spec = SPECS[key]
    u = make_bare_updater(spec)
    field0 = {"lat": [0], "lon": [0], "values": [[1.0]]}
    state = ForecastState.at_hour("2026-06-13", "18", 3)

    with patch("worldmap.tasks.scalar_field.Plot") as MockPlot, patch(
        "worldmap.tasks.scalar_field.encode_frames"
    ) as mock_encode:
        u.plot(field0, state)

    # contourf gets this spec's extend mode (and the shared levels/zorder).
    contourf = MockPlot.return_value.ax.contourf
    contourf.assert_called_once()
    assert contourf.call_args.kwargs["extend"] == spec.extend
    assert contourf.call_args.kwargs["levels"] == 20
    assert contourf.call_args.kwargs["zorder"] == 2

    # The colourbar key gets this spec's ticks + title.
    u.save_key_image.assert_called_once()
    key_args = u.save_key_image.call_args
    assert key_args.args[0] == u.output_path
    assert key_args.args[3] == spec.ticks
    assert key_args.args[4] == spec.title

    # The data texture is scaled to this spec's value range.
    assert mock_encode.call_args.args[2] == spec.vmin
    assert mock_encode.call_args.args[3] == spec.vmax


def test_plot_skips_when_field_missing():
    u = make_bare_updater(SPECS["temperature"])
    state = ForecastState.at_hour("2026-06-13", "18", 3)
    with patch("worldmap.tasks.scalar_field.Plot") as MockPlot, patch(
        "worldmap.tasks.scalar_field.encode_frames"
    ):
        u.plot({"lat": [0], "lon": [0], "values": None}, state)
    MockPlot.assert_not_called()
    u.save_key_image.assert_not_called()


def test_specs_cover_the_three_scalar_fields():
    assert set(SPECS) == {"temperature", "ozone", "stormwatch"}
    for key, spec in SPECS.items():
        assert isinstance(spec, ScalarFieldSpec)
        assert spec.product == key


def test_layer_builder_binds_each_spec_via_partial():
    from worldmap.layer_builder import TASK_CLASSES

    for key in SPECS:
        entry = TASK_CLASSES[key]
        assert isinstance(entry, partial)
        assert entry.func is ScalarFieldUpdater
        assert entry.keywords["spec"] is SPECS[key]
