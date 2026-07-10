#!/usr/bin/env python3
"""Regression tests for the SST Absolute<->Anomaly mode-switch bug, and for the
follow-up design change that supersedes half of that fix: both modes are now rendered
to permanent, independent output paths every cycle (sst_absolute.png/sst_anomaly.png),
not just whichever mode is currently configured -- so the frontend (ui/modules/sst.js)
can switch between them instantly, and the config's `mode` setting only controls which
one gets published to the stable, run-agnostic sst.png/sst_key.png for anything still
reading that name directly.

Original two compounding causes:
  * SstCollector.collect() only ever downloaded the netCDF for whichever mode was
    CURRENTLY configured -- switching modes left the other mode's source file
    completely un-downloaded until the next is_stale() cadence (up to
    runs_per_day-derived hours later), which has no awareness that `mode` changed.
    (Still true today -- covered below.)
  * SSTUpdater.run() wrote both modes to the SAME output path and decided whether to
    re-render purely by comparing that file's mtime against the source netCDF's mtime
    -- it had no way to know "this file was last rendered in a DIFFERENT mode than the
    one now configured". This is now moot: each mode has always had its own,
    independently-freshness-checked output path, so there's nothing to disambiguate.
"""
import os
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.sst import SstCollector
from atmos_gl.tasks.sst import SSTUpdater


def make_bare_sst_collector(settings=None, workdir="."):
    c = SstCollector.__new__(SstCollector)
    c.settings = settings or {}
    c.config = MagicMock()
    c.config.get_setting.return_value = workdir
    return c


def test_collect_fetches_both_modes_regardless_of_which_is_configured(tmp_path):
    """The bug: only the configured mode's netCDF ever got downloaded. Fixed
    collect() must warm BOTH mode caches every cycle, mirroring the
    "collection is unconditional" pattern collect_event_feeds() already uses for
    `enabled` -- here applied to `mode` instead."""
    c = make_bare_sst_collector(
        settings={"url": "https://example.com/oisst", "mode": "absolute"},
        workdir=str(tmp_path),
    )

    with patch("atmos_gl.collectors.sst.remote_is_newer", return_value=True), \
         patch("atmos_gl.collectors.sst.download_whole", return_value=b"fake-netcdf-bytes"):
        c.collect()

    assert (tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc").exists()
    assert (tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc").exists()


def test_collect_raises_if_any_mode_fails_so_data_status_reflects_it(tmp_path):
    """_drive() (collectors/__init__.py) only records success=True/updates
    last_updated when collect() returns without raising -- if a per-mode download
    failure is swallowed internally (logged and returned from), the Data Status UI
    would show 100% even though one mode's cache never got refreshed. Both modes
    must still be ATTEMPTED (one failing shouldn't block the other), but collect()
    must raise afterward so the failure is visible."""
    c = make_bare_sst_collector(
        settings={"url": "https://example.com/oisst", "mode": "absolute"},
        workdir=str(tmp_path),
    )

    def fake_download(url, timeout=300):
        if "anom" in url:
            raise RuntimeError("503 Service Unavailable")
        return b"fake-netcdf-bytes"

    with patch("atmos_gl.collectors.sst.remote_is_newer", return_value=True), \
         patch("atmos_gl.collectors.sst.download_whole", side_effect=fake_download):
        try:
            c.collect()
            raised = False
        except Exception:
            raised = True

    assert raised, "collect() must raise if any mode failed, so _drive() records success=False"
    # The OTHER mode must still have been attempted and succeeded despite the failure.
    assert (tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc").exists()
    assert not (tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc").exists()


def test_collect_skips_a_mode_whose_remote_is_not_newer(tmp_path):
    """Per-mode freshness is still independent -- one mode being up to date
    shouldn't block or skip the other."""
    c = make_bare_sst_collector(
        settings={"url": "https://example.com/oisst", "mode": "absolute"},
        workdir=str(tmp_path),
    )

    def fake_remote_is_newer(url, dest):
        return "anomaly" in dest  # only anomaly needs fetching

    with patch("atmos_gl.collectors.sst.remote_is_newer", side_effect=fake_remote_is_newer), \
         patch("atmos_gl.collectors.sst.download_whole", return_value=b"fake-netcdf-bytes") as mock_dl:
        c.collect()

    assert mock_dl.call_count == 1
    assert (tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc").exists()
    assert not (tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc").exists()


def make_bare_sst_updater(mode, workdir, output_path):
    u = SSTUpdater.__new__(SSTUpdater)
    u.mode = mode
    u.workdir = workdir
    u.section = "sst"
    u.output_path = output_path
    u.settings = {}
    u.plot = MagicMock()
    u._publish_current_mode = MagicMock()
    return u


def _touch(path, mtime_offset=0):
    with open(path, "w") as f:
        f.write("x")
    if mtime_offset:
        t = os.path.getmtime(path) + mtime_offset
        os.utime(path, (t, t))


def test_run_renders_both_modes_to_separate_persistent_paths(tmp_path):
    (tmp_path / "data").mkdir()
    abs_nc = tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc"
    anom_nc = tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc"
    _touch(abs_nc)
    _touch(anom_nc)
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("absolute", str(tmp_path), str(out_path))
    u.run()

    assert u.plot.call_count == 2
    called_modes = {call.args[0] for call in u.plot.call_args_list}
    assert called_modes == {"absolute", "anomaly"}
    called_outputs = {call.args[2] for call in u.plot.call_args_list}
    assert called_outputs == {
        str(tmp_path / "data" / "sst_absolute.png"),
        str(tmp_path / "data" / "sst_anomaly.png"),
    }


def test_run_skips_a_mode_whose_output_is_already_fresh(tmp_path):
    (tmp_path / "data").mkdir()
    abs_nc = tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc"
    anom_nc = tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc"
    _touch(abs_nc)
    _touch(anom_nc)
    # Absolute already has a fresh render; anomaly doesn't yet.
    _touch(tmp_path / "data" / "sst_absolute.png", mtime_offset=10)
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("absolute", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_called_once()
    assert u.plot.call_args.args[0] == "anomaly"


def test_run_does_not_render_when_both_modes_already_fresh(tmp_path):
    (tmp_path / "data").mkdir()
    for mode, nc_name in [("absolute", "noaa_oisst_mean.nc"), ("anomaly", "noaa_oisst_anomaly.nc")]:
        _touch(tmp_path / "data" / f"sst_cache_{nc_name}")
        _touch(tmp_path / "data" / f"sst_{mode}.png", mtime_offset=10)
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("anomaly", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_not_called()
    # Publishing still happens every cycle regardless of whether a render occurred.
    u._publish_current_mode.assert_called_once_with(str(tmp_path / "data" / "sst_anomaly.png"))


def test_run_publishes_only_the_currently_configured_mode(tmp_path):
    (tmp_path / "data").mkdir()
    _touch(tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc")
    _touch(tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc")
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("anomaly", str(tmp_path), str(out_path))
    u.run()

    u._publish_current_mode.assert_called_once_with(str(tmp_path / "data" / "sst_anomaly.png"))


def test_run_skips_a_mode_whose_source_cache_is_missing_without_blocking_the_other(tmp_path):
    (tmp_path / "data").mkdir()
    abs_nc = tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc"
    _touch(abs_nc)  # anomaly netCDF not fetched yet
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("absolute", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_called_once()
    assert u.plot.call_args.args[0] == "absolute"
    u._publish_current_mode.assert_called_once()


def test_run_publishes_nothing_when_configured_modes_source_is_missing(tmp_path):
    """Configured mode is anomaly, but only the absolute netCDF exists -- absolute
    renders, but nothing is published to the stable filename since the configured
    mode's own data isn't ready."""
    (tmp_path / "data").mkdir()
    _touch(tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc")
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("anomaly", str(tmp_path), str(out_path))
    u.run()

    u._publish_current_mode.assert_not_called()
