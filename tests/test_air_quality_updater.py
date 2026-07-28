#!/usr/bin/env python3
"""AirQualityUpdater.run() orchestration: renders all 3 variables (pm2_5, pm10, aod)
every cycle, skipping ones already fresh, and publishes only the currently-configured
variable -- directly mirroring tests/test_greenhouse_gases_updater.py's GhgUpdater
tests, minus the anomaly/baseline axis (this layer is Absolute-only). plot() itself is
mocked throughout: this seam tests orchestration (what gets rendered/skipped/
published), not rendering internals (netCDF parsing, unit conversion, matplotlib).
"""
import os
from unittest.mock import MagicMock

from atmos_gl.lib.air_quality import camsforecast_cache_path
from atmos_gl.tasks.air_quality import AirQualityUpdater


def make_bare_aq_updater(variable, workdir, output_path):
    u = AirQualityUpdater.__new__(AirQualityUpdater)
    u.variable = variable
    u.workdir = workdir
    u.section = "air_quality"
    u.output_path = output_path
    u.settings = {}
    u.common = {}
    u.plot = MagicMock()
    u._publish_current = MagicMock()
    return u


def _touch(path, mtime_offset=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")
    if mtime_offset:
        t = os.path.getmtime(path) + mtime_offset
        os.utime(path, (t, t))


def _current_nc(tmp_path):
    return camsforecast_cache_path(str(tmp_path))


def test_run_renders_all_three_variables_to_separate_paths(tmp_path):
    _touch(_current_nc(tmp_path))
    out_path = tmp_path / "data" / "air_quality.png"

    u = make_bare_aq_updater("pm2_5", str(tmp_path), str(out_path))
    u.run()

    assert u.plot.call_count == 3
    called = {call.args[0] for call in u.plot.call_args_list}
    assert called == {"pm2_5", "pm10", "aod"}


def test_run_skips_when_current_cache_missing(tmp_path):
    out_path = tmp_path / "data" / "air_quality.png"
    u = make_bare_aq_updater("pm2_5", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_not_called()
    u._publish_current.assert_not_called()


def test_run_skips_a_variable_whose_output_is_already_fresh(tmp_path):
    _touch(_current_nc(tmp_path))
    out_path = tmp_path / "data" / "air_quality.png"

    u = make_bare_aq_updater("pm2_5", str(tmp_path), str(out_path))
    pm25_out = tmp_path / "data" / "air_quality_pm2_5.png"
    _touch(pm25_out, mtime_offset=10)
    u._write_render_signature(str(pm25_out), u._variable_settings_signature("pm2_5"))

    u.run()

    called = {call.args[0] for call in u.plot.call_args_list}
    assert "pm2_5" not in called
    assert len(called) == 2


def test_run_re_renders_a_variable_whose_output_is_data_fresh_but_settings_changed(tmp_path):
    """The bug this closes (mirrors GhgUpdater's equivalent test): a scale-only
    config change (no new source data) must still force a re-render, not sit stale
    until the CAMS forecast cache next refreshes."""
    _touch(_current_nc(tmp_path))
    out_path = tmp_path / "data" / "air_quality.png"

    u = make_bare_aq_updater("pm2_5", str(tmp_path), str(out_path))
    pm25_out = tmp_path / "data" / "air_quality_pm2_5.png"
    _touch(pm25_out, mtime_offset=10)
    u._write_render_signature(str(pm25_out), "stale-signature-from-a-different-scale")

    u.run()

    called = {call.args[0] for call in u.plot.call_args_list}
    assert "pm2_5" in called


def test_run_publishes_only_the_currently_configured_variable(tmp_path):
    _touch(_current_nc(tmp_path))
    out_path = tmp_path / "data" / "air_quality.png"

    u = make_bare_aq_updater("aod", str(tmp_path), str(out_path))
    u.run()

    u._publish_current.assert_called_once_with(
        str(tmp_path / "data" / "air_quality_aod.png")
    )
