#!/usr/bin/env python3
"""FloodRiskUpdater.run() orchestration: both Live (per-forecast-hour) and
Historical (single-shot) render every cycle regardless of the configured mode,
but only the currently-configured mode's output is published to the stable base
filename -- directly mirrors tests/test_greenhouse_gases_updater.py's species/mode
matrix, collapsed to Flood Risk's 2-way mode toggle. render_all_hours/_render_historical
are mocked throughout for the orchestration tests: this seam tests what gets
rendered/published, not rendering internals (covered separately below by
test_plot_live_writes_a_severity_texture / test_render_historical_writes_a_hazard_texture).
"""
import os
from unittest.mock import MagicMock

import numpy as np

from atmos_gl.lib.flood_risk import jrc_hazard_mosaic_cache_path, save_jrc_hazard_mosaic
from atmos_gl.tasks.common import ForecastState
from atmos_gl.tasks.flood_risk import (
    _HISTORICAL_ENCODE_DOMAIN,
    _LIVE_ENCODE_DOMAIN,
    FloodRiskUpdater,
)


def make_bare_flood_risk_updater(mode, workdir, output_path):
    u = FloodRiskUpdater.__new__(FloodRiskUpdater)
    u.mode = mode
    u.workdir = workdir
    u.section = "flood_risk"
    u.output_path = output_path
    u.settings = {"mode": mode}
    u.common = {}
    u.render_all_hours = MagicMock(return_value=0)
    u._render_historical = MagicMock(return_value=None)
    u._publish_variant = MagicMock()
    return u


def test_run_always_renders_live_hours_regardless_of_configured_mode(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("historical", str(tmp_path), str(out_path))

    u.run()

    u.render_all_hours.assert_called_once()
    args, kwargs = u.render_all_hours.call_args
    assert args[0] == "flood_risk_live"
    assert kwargs["plot_fn"] == u._plot_live


def test_run_always_renders_historical_regardless_of_configured_mode(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("live", str(tmp_path), str(out_path))

    u.run()

    u._render_historical.assert_called_once()


def test_run_swaps_output_path_to_the_live_variant_only_for_render_all_hours(tmp_path):
    """render_all_hours must see the '_live' variant path (so its own internal
    publish_current_hour can never clobber self.output_path directly), and
    self.output_path must be restored to the canonical name afterward."""
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("live", str(tmp_path), str(out_path))

    seen_output_path = {}

    def fake_render_all_hours(*args, **kwargs):
        seen_output_path["value"] = u.output_path
        return 0

    u.render_all_hours.side_effect = fake_render_all_hours

    u.run()

    assert seen_output_path["value"] == str(tmp_path / "data" / "flood_risk_live.png")
    assert u.output_path == str(out_path)


def test_run_publishes_the_live_variant_when_mode_is_live_and_it_exists(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("live", str(tmp_path), str(out_path))
    live_variant = tmp_path / "data" / "flood_risk_live.png"
    os.makedirs(live_variant.parent, exist_ok=True)
    live_variant.write_bytes(b"x")

    u.run()

    u._publish_variant.assert_called_once_with(str(live_variant))


def test_run_publishes_nothing_when_mode_is_live_but_no_live_render_exists_yet(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("live", str(tmp_path), str(out_path))

    u.run()

    u._publish_variant.assert_not_called()


def test_run_publishes_the_historical_variant_when_mode_is_historical(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("historical", str(tmp_path), str(out_path))
    historical_variant = str(tmp_path / "data" / "flood_risk_historical.png")
    u._render_historical.return_value = historical_variant

    u.run()

    u._publish_variant.assert_called_once_with(historical_variant)


def test_run_publishes_nothing_when_mode_is_historical_but_mosaic_not_cached_yet(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = make_bare_flood_risk_updater("historical", str(tmp_path), str(out_path))
    u._render_historical.return_value = None  # mosaic not cached yet

    u.run()

    u._publish_variant.assert_not_called()


def test_status_product_is_the_live_product_only_when_mode_is_live():
    u = FloodRiskUpdater.__new__(FloodRiskUpdater)
    u.settings = {"mode": "live"}
    u.mode = "live"
    u.status_product = "flood_risk_live" if u.mode == "live" else None
    assert u.status_product == "flood_risk_live"

    u.mode = "historical"
    u.status_product = "flood_risk_live" if u.mode == "live" else None
    assert u.status_product is None


# ---- direct rendering tests (not orchestration) --------------------------------


def _bare_updater_for_rendering(workdir, output_path):
    u = FloodRiskUpdater.__new__(FloodRiskUpdater)
    u.workdir = workdir
    u.section = "flood_risk"
    u.output_path = output_path
    u.settings = {}
    return u


def test_plot_live_writes_a_severity_texture_within_the_live_encode_domain(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    os.makedirs(out_path.parent, exist_ok=True)
    u = _bare_updater_for_rendering(str(tmp_path), str(out_path))
    field0 = {
        "lat": np.array([1.0, 0.0]),
        "lon": np.array([0.0, 1.0]),
        "values": np.array([[0.0, 3.0], [1.0, 2.0]], dtype=np.float32),
    }
    state = ForecastState.at_hour("20260830", "00", 24)

    u._plot_live(field0, state)

    expected = tmp_path / "data" / "flood_risk_f024.png"
    assert expected.exists()
    assert _LIVE_ENCODE_DOMAIN == (0.0, 3.0)  # RETURN_PERIODS_YEARS has 3 tiers


def test_render_historical_writes_a_texture_when_mosaic_is_cached(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = _bare_updater_for_rendering(str(tmp_path), str(out_path))
    mosaic_path = jrc_hazard_mosaic_cache_path(str(tmp_path))
    band = np.array([[0, 1], [2, 4]], dtype=np.uint8)
    save_jrc_hazard_mosaic(mosaic_path, band, np.array([1.0, 0.0]), np.array([0.0, 1.0]))

    out = u._render_historical()

    assert out == str(tmp_path / "data" / "flood_risk_historical.png")
    assert os.path.exists(out)
    assert os.path.exists(out + ".sig")
    assert _HISTORICAL_ENCODE_DOMAIN == (0.0, 4.0)


def test_render_historical_returns_none_when_mosaic_not_cached(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = _bare_updater_for_rendering(str(tmp_path), str(out_path))

    assert u._render_historical() is None


def test_render_historical_skips_re_render_when_already_fresh(tmp_path):
    out_path = tmp_path / "data" / "flood_risk.png"
    u = _bare_updater_for_rendering(str(tmp_path), str(out_path))
    mosaic_path = jrc_hazard_mosaic_cache_path(str(tmp_path))
    band = np.array([[0, 1], [2, 4]], dtype=np.uint8)
    save_jrc_hazard_mosaic(mosaic_path, band, np.array([1.0, 0.0]), np.array([0.0, 1.0]))

    first = u._render_historical()
    first_mtime = os.path.getmtime(first)

    second = u._render_historical()

    assert second == first
    assert os.path.getmtime(second) == first_mtime
