#!/usr/bin/env python3
"""GhgUpdater.run() orchestration: renders all 4 species x mode combinations every
cycle, skipping ones already fresh, and publishes only the currently-configured
(species, mode) pair -- directly mirroring tests/test_sst_mode_switch.py's SSTUpdater
tests, extended from SST's 2-mode matrix to GHG's 2 species x 2 modes. plot() itself
is mocked throughout: this seam tests orchestration (what gets rendered/skipped/
published), not rendering internals (netCDF parsing, unit conversion, matplotlib).
"""
import os
from unittest.mock import MagicMock

from atmos_gl.tasks.greenhouse_gases import GhgUpdater


def make_bare_ghg_updater(species, mode, workdir, output_path, baseline_year=2003):
    u = GhgUpdater.__new__(GhgUpdater)
    u.species = species
    u.mode = mode
    u.workdir = workdir
    u.section = "greenhouse_gases"
    u.output_path = output_path
    u.settings = {"baseline_year": baseline_year}
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


def _geoscf_nc(tmp_path):
    return tmp_path / "data" / "greenhouse_gases_cache_geoscf.nc"


def _egg4_nc(tmp_path, year=2003):
    return tmp_path / "data" / f"greenhouse_gases_cache_egg4_{year}.nc"


def test_run_renders_all_four_species_mode_combinations_to_separate_paths(tmp_path):
    _touch(_geoscf_nc(tmp_path))
    _touch(_egg4_nc(tmp_path))
    out_path = tmp_path / "data" / "greenhouse_gases.png"

    u = make_bare_ghg_updater("co2", "absolute", str(tmp_path), str(out_path))
    u.run()

    assert u.plot.call_count == 4
    called = {(call.args[0], call.args[1]) for call in u.plot.call_args_list}
    assert called == {
        ("co2", "absolute"), ("co2", "anomaly"),
        ("ch4", "absolute"), ("ch4", "anomaly"),
    }


def test_run_skips_when_geoscf_cache_missing(tmp_path):
    out_path = tmp_path / "data" / "greenhouse_gases.png"
    u = make_bare_ghg_updater("co2", "absolute", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_not_called()
    u._publish_current.assert_not_called()


def test_run_skips_anomaly_combinations_when_baseline_not_yet_cached(tmp_path):
    _touch(_geoscf_nc(tmp_path))
    # No EGG4 baseline cache -- first boot / not fetched yet for this baseline_year.
    out_path = tmp_path / "data" / "greenhouse_gases.png"

    u = make_bare_ghg_updater("co2", "absolute", str(tmp_path), str(out_path))
    u.run()

    called = {(call.args[0], call.args[1]) for call in u.plot.call_args_list}
    assert called == {("co2", "absolute"), ("ch4", "absolute")}


def test_run_skips_a_combination_whose_output_is_already_fresh(tmp_path):
    _touch(_geoscf_nc(tmp_path))
    _touch(_egg4_nc(tmp_path))
    _touch(tmp_path / "data" / "greenhouse_gases_co2_absolute.png", mtime_offset=10)
    out_path = tmp_path / "data" / "greenhouse_gases.png"

    u = make_bare_ghg_updater("co2", "absolute", str(tmp_path), str(out_path))
    u.run()

    called = {(call.args[0], call.args[1]) for call in u.plot.call_args_list}
    assert ("co2", "absolute") not in called
    assert len(called) == 3


def test_run_publishes_only_the_currently_configured_species_and_mode(tmp_path):
    _touch(_geoscf_nc(tmp_path))
    _touch(_egg4_nc(tmp_path))
    out_path = tmp_path / "data" / "greenhouse_gases.png"

    u = make_bare_ghg_updater("ch4", "anomaly", str(tmp_path), str(out_path))
    u.run()

    u._publish_current.assert_called_once_with(
        str(tmp_path / "data" / "greenhouse_gases_ch4_anomaly.png")
    )


def test_run_publishes_nothing_when_configured_mode_is_anomaly_but_baseline_missing(tmp_path):
    _touch(_geoscf_nc(tmp_path))
    # No EGG4 baseline -- configured mode is anomaly, so nothing is published even
    # though absolute renders happen for both species.
    out_path = tmp_path / "data" / "greenhouse_gases.png"

    u = make_bare_ghg_updater("co2", "anomaly", str(tmp_path), str(out_path))
    u.run()

    u._publish_current.assert_not_called()
