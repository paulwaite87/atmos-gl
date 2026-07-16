#!/usr/bin/env python3
"""Tests for FireWeatherUpdater (tasks/fire_weather.py). Unlike every ScalarFieldSpec
entry in tests/test_scalar_field.py, this task deliberately decouples its config
section ("fires", shared with the FIRMS collector/route) from its fieldstore product
("fire_weather") -- these tests exist specifically to pin that decoupling, since
ScalarFieldUpdater.__init__ normally forces them to be the same string.
"""
from unittest.mock import patch, MagicMock

from atmos_gl.tasks.common import ForecastState
from atmos_gl.tasks.fire_weather import FireWeatherUpdater, FIRE_WEATHER_SPEC
from atmos_gl.tasks.scalar_field import ScalarFieldUpdater


def make_bare_fire_weather_updater():
    """Bypass Updater.__init__ (does config/IO), mirroring
    test_scalar_field.py's make_bare_updater."""
    u = FireWeatherUpdater.__new__(FireWeatherUpdater)
    u.spec = FIRE_WEATHER_SPEC
    u.section = "fires"
    u.status_product = "fire_weather"
    u.settings = {}
    u.map_region_bbox = (-180, -90, 180, 90)
    u.output_path = "/tmp/out/fires.png"
    u.map_data = MagicMock()
    u.map_data.region.region_identifier = "global"
    u.regrid_for_lod = MagicMock(return_value=([0], [0], [[0]]))
    u.get_output_path_for_hour = MagicMock(return_value="/tmp/out/fires_f003.png")
    u.save_key_image = MagicMock()
    return u


def test_init_decouples_section_from_fieldstore_product():
    """The whole reason this task exists instead of a plain SPECS entry: section
    ("fires", shared with the DB collector's config) and status_product/fieldstore key
    ("fire_weather", distinct) must differ."""
    config = MagicMock()
    config.get_section.return_value = {"level_of_detail": "2"}
    map_data = MagicMock()

    with patch("atmos_gl.tasks.common.fieldstore.make_store"), patch(
        "atmos_gl.tasks.common.ProcessStatusAdapter"
    ):
        u = FireWeatherUpdater(config, map_data)

    assert u.section == "fires"
    assert u.status_product == "fire_weather"
    assert u.spec.product == "fire_weather"
    assert u.spec is FIRE_WEATHER_SPEC
    assert u.level_of_detail == 2
    assert u.per_hour_outputs == [".png", "_data.png"]


def test_is_a_scalar_field_updater_and_reuses_its_render_path():
    """plot()/_resolve_cmap()/_write_legend_key()/run() must all be the inherited
    ScalarFieldUpdater implementations, unchanged -- this task overrides ONLY __init__."""
    assert issubclass(FireWeatherUpdater, ScalarFieldUpdater)
    assert FireWeatherUpdater.plot is ScalarFieldUpdater.plot
    assert FireWeatherUpdater.run is ScalarFieldUpdater.run
    assert FireWeatherUpdater._resolve_cmap is ScalarFieldUpdater._resolve_cmap
    assert FireWeatherUpdater._write_legend_key is ScalarFieldUpdater._write_legend_key


def test_plot_dispatches_the_fire_weather_spec():
    u = make_bare_fire_weather_updater()
    field0 = {"lat": [0], "lon": [0], "values": [[42.0]]}
    state = ForecastState.at_hour("2026-06-13", "18", 3)

    with patch("atmos_gl.tasks.scalar_field.Plot") as MockPlot, patch(
        "atmos_gl.tasks.scalar_field.encode_frames"
    ) as mock_encode:
        u.plot(field0, state)

    contourf = MockPlot.return_value.ax.contourf
    contourf.assert_called_once()
    assert contourf.call_args.kwargs["extend"] == "max"
    assert mock_encode.call_args.args[2] == 0.0
    assert mock_encode.call_args.args[3] == 100.0


def test_resolve_cmap_uses_the_single_fixed_palette_by_default():
    """No ("fires", "palette") setting is exposed in config -- must fall back to the
    spec's own palette_default regardless of what's in settings."""
    u = make_bare_fire_weather_updater()
    u.settings = {}
    cmap = u._resolve_cmap()
    pale_yellow = (1.0, 0.95, 0.6)
    deep_red = (0.6, 0.0, 0.0)
    import pytest

    # Default threshold (min_risk_display) is 25/100 -- the palette's first colour
    # anchors that boundary; below it renders flat/transparent (see the threshold test).
    t = 25.0 / 100.0
    assert cmap(t)[:3] == pytest.approx(pale_yellow, abs=0.02)
    assert cmap(1.0)[:3] == pytest.approx(deep_red, abs=0.02)


def test_resolve_cmap_reads_live_min_risk_display_threshold():
    u = make_bare_fire_weather_updater()
    u.settings = {"min_risk_display": 50.0}
    cmap = u._resolve_cmap()
    pale_yellow = (1.0, 0.95, 0.6)
    import pytest

    t = 50.0 / 100.0
    assert cmap(t)[:3] == pytest.approx(pale_yellow, abs=0.02)
    assert cmap(t - 0.1) == pytest.approx((0.0, 0.0, 0.0, 0.0), abs=0.02)


def test_layer_builder_registers_fires_as_fire_weather_updater():
    from atmos_gl.layer_builder import TASK_CLASSES

    assert TASK_CLASSES["fires"] is FireWeatherUpdater
