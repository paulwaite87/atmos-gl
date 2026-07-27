#!/usr/bin/env python3
"""GeosCfGhgCollector: downloads NASA GEOS-CF's latest CO2+CH4 snapshot into the
greenhouse_gases layer's file cache. Same test seam as tests/test_sst_mode_switch.py's
SstCollector tests -- mock the HTTP download boundary (download_whole), assert on
cache file existence and the constructed URL, not on netCDF contents.
"""
from unittest.mock import MagicMock, patch

from atmos_gl.collectors.greenhouse_gases import GeosCfGhgCollector, build_geoscf_url


def make_bare_geoscf_collector(settings=None, workdir="."):
    c = GeosCfGhgCollector.__new__(GeosCfGhgCollector)
    c.settings = settings or {}
    url = c.settings.get("geoscf_url", "https://opendap.example.com/fcast")

    def fake_get_setting(section, key, default=None):
        if section == "data_collector" and key == "datasources":
            return {"geoscf": url}
        if section == "common" and key == "workdir":
            return workdir
        return default

    c.config = MagicMock()
    c.config.get_setting.side_effect = fake_get_setting
    return c


def test_build_geoscf_url_targets_the_latest_snapshot_with_co2_and_ch4():
    url = build_geoscf_url("https://opendap.example.com/fcast")
    assert url.startswith("https://opendap.example.com/fcast/")
    assert ".latest.nc4?" in url
    assert "CO2[" in url
    assert "CH4[" in url


def test_collect_downloads_and_caches_the_latest_snapshot(tmp_path):
    c = make_bare_geoscf_collector(workdir=str(tmp_path))

    with patch(
        "atmos_gl.collectors.greenhouse_gases.download_whole",
        return_value=b"fake-netcdf-bytes",
    ) as mock_dl:
        c.collect()

    dest = tmp_path / "data" / "greenhouse_gases_cache_geoscf.nc"
    assert dest.exists()
    assert dest.read_bytes() == b"fake-netcdf-bytes"
    mock_dl.assert_called_once()
    assert ".latest.nc4?" in mock_dl.call_args[0][0]


def test_collect_skips_without_raising_when_no_datasource_configured(tmp_path):
    c = make_bare_geoscf_collector(workdir=str(tmp_path))
    c.config.get_setting.side_effect = lambda section, key, default=None: (
        {} if section == "data_collector" and key == "datasources" else default
    )

    with patch("atmos_gl.collectors.greenhouse_gases.download_whole") as mock_dl:
        c.collect()

    mock_dl.assert_not_called()
    assert not (tmp_path / "data").exists()
