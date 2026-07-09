#!/usr/bin/env python3
"""Regression tests for the SST Absolute<->Anomaly mode-switch bug: switching
`sst.mode` in the config kept showing the old mode's PNG + key indefinitely.

Two compounding causes, both covered here:
  * SstCollector.collect() only ever downloaded the netCDF for whichever mode was
    CURRENTLY configured -- switching modes left the other mode's source file
    completely un-downloaded until the next is_stale() cadence (up to
    runs_per_day-derived hours later), which has no awareness that `mode` changed.
  * SSTUpdater.run() wrote both modes to the SAME output path and decided whether to
    re-render purely by comparing that file's mtime against the source netCDF's mtime
    -- it had no way to know "this file was last rendered in a DIFFERENT mode than the
    one now configured", so a mode switch wasn't guaranteed to trigger a re-render even
    once the right source data existed.
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
    return u


def _touch(path, mtime_offset=0):
    with open(path, "w") as f:
        f.write("x")
    if mtime_offset:
        t = os.path.getmtime(path) + mtime_offset
        os.utime(path, (t, t))


def test_run_skips_when_mode_unchanged_and_output_already_fresh(tmp_path):
    (tmp_path / "data").mkdir()
    nc_path = tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc"
    out_path = tmp_path / "data" / "sst.png"
    _touch(nc_path)
    _touch(out_path, mtime_offset=10)  # newer than source

    u = make_bare_sst_updater("absolute", str(tmp_path), str(out_path))
    marker = tmp_path / "data" / "sst_cache_last_mode.txt"
    marker.write_text("absolute")

    u.run()

    u.plot.assert_not_called()


def test_run_rerenders_when_mode_changed_even_if_output_is_newer_than_source(tmp_path):
    """The core regression: output already exists and is NEWER than the source
    netCDF (the old code's only freshness signal), but it was last rendered in a
    different mode than the one now configured. The fix must re-render anyway."""
    (tmp_path / "data").mkdir()
    nc_path = tmp_path / "data" / "sst_cache_noaa_oisst_anomaly.nc"
    out_path = tmp_path / "data" / "sst.png"
    _touch(nc_path)
    _touch(out_path, mtime_offset=10)  # newer than source -- "fresh" by mtime alone

    marker = tmp_path / "data" / "sst_cache_last_mode.txt"
    marker.write_text("absolute")  # out.png was last rendered in absolute mode

    u = make_bare_sst_updater("anomaly", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_called_once()
    assert marker.read_text().strip() == "anomaly"


def test_run_renders_on_first_run_with_no_prior_marker(tmp_path):
    (tmp_path / "data").mkdir()
    nc_path = tmp_path / "data" / "sst_cache_noaa_oisst_mean.nc"
    out_path = tmp_path / "data" / "sst.png"
    _touch(nc_path)

    u = make_bare_sst_updater("absolute", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_called_once()
    marker = tmp_path / "data" / "sst_cache_last_mode.txt"
    assert marker.read_text().strip() == "absolute"


def test_run_skips_when_source_cache_missing(tmp_path):
    (tmp_path / "data").mkdir()
    out_path = tmp_path / "data" / "sst.png"

    u = make_bare_sst_updater("anomaly", str(tmp_path), str(out_path))
    u.run()

    u.plot.assert_not_called()
