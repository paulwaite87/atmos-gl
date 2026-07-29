#!/usr/bin/env python3
"""retrieve_and_unzip: shared submit-then-poll-then-unzip mechanics behind every
CDS-backed collector (CamsGhgForecastCollector, CamsEgg4BaselineCollector,
AirQualityCollector) -- every CDS dataset in this app delivers data_format=netcdf_zip
(a zip archive), not a raw netCDF, so the fetch isn't complete until the archive's .nc
member is extracted to the real cache path.

retrieve_with_day_fallback: shared "today's run isn't always published yet" search
behind every CDS-backed FORECAST collector (CamsGhgForecastCollector,
AirQualityCollector -- not CamsEgg4BaselineCollector, which fetches a fixed historical
year with no publish-lag concept). Extracted out of collectors/greenhouse_gases.py's
CamsGhgForecastCollector.collect(), which duplicated this loop verbatim before
AirQualityCollector copy-pasted it a second time -- covered indirectly by both
collectors' own test suites (test_greenhouse_gases_forecast_collector.py,
test_air_quality_collector.py), plus directly here.
"""
import concurrent.futures
import os
from unittest.mock import MagicMock, patch

import pytest

from atmos_gl.lib.cds_client import retrieve_and_unzip, retrieve_with_day_fallback


def test_retrieve_and_unzip_extracts_the_nc_member_to_cache_dest(tmp_path, make_netcdf_zip_bytes):
    zip_bytes = make_netcdf_zip_bytes("some_download_name.nc", b"real-netcdf-bytes")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    client = MagicMock()
    client.retrieve.side_effect = fake_retrieve
    dest = str(tmp_path / "data" / "cached.nc")

    retrieve_and_unzip(client, "some-dataset", {}, dest, timeout_s=5, label="test")

    assert os.path.exists(dest)
    assert open(dest, "rb").read() == b"real-netcdf-bytes"


def test_retrieve_and_unzip_raises_when_archive_has_no_nc_member(tmp_path):
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", b"not a netcdf")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(buf.getvalue())

    client = MagicMock()
    client.retrieve.side_effect = fake_retrieve
    dest = str(tmp_path / "data" / "cached.nc")

    with pytest.raises(RuntimeError, match="no .nc file"):
        retrieve_and_unzip(client, "some-dataset", {}, dest, timeout_s=5, label="test")

    assert not os.path.exists(dest)


def _build_request(date_str):
    return {"date": f"{date_str}/{date_str}"}


def test_retrieve_with_day_fallback_succeeds_on_the_first_day(tmp_path, make_netcdf_zip_bytes):
    zip_bytes = make_netcdf_zip_bytes("data.nc", b"todays-run")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    client = MagicMock()
    client.retrieve.side_effect = fake_retrieve
    dest = str(tmp_path / "data" / "cached.nc")

    ok = retrieve_with_day_fallback(
        client, "some-dataset", _build_request, dest, timeout_s=5, search_days=3, label="test"
    )

    assert ok is True
    assert client.retrieve.call_count == 1
    assert open(dest, "rb").read() == b"todays-run"


def test_retrieve_with_day_fallback_tries_an_earlier_date_when_the_first_fails(
    tmp_path, make_netcdf_zip_bytes
):
    zip_bytes = make_netcdf_zip_bytes("data.nc", b"yesterdays-run")
    seen_dates = []

    def fake_retrieve(dataset, request, target):
        date_str = request["date"].split("/")[0]
        seen_dates.append(date_str)
        if len(seen_dates) == 1:
            raise RuntimeError("400 Client Error: today's run not published yet")
        with open(target, "wb") as f:
            f.write(zip_bytes)

    client = MagicMock()
    client.retrieve.side_effect = fake_retrieve
    dest = str(tmp_path / "data" / "cached.nc")

    ok = retrieve_with_day_fallback(
        client, "some-dataset", _build_request, dest, timeout_s=5, search_days=3, label="test"
    )

    assert ok is True
    assert len(seen_dates) == 2
    assert seen_dates[1] < seen_dates[0]  # tried an earlier date second
    assert open(dest, "rb").read() == b"yesterdays-run"


def test_retrieve_with_day_fallback_stops_at_a_timeout_without_trying_earlier_dates(tmp_path):
    client = MagicMock()
    client.retrieve.side_effect = RuntimeError("unused")
    dest = str(tmp_path / "data" / "cached.nc")

    with patch(
        "atmos_gl.lib.cds_client.retrieve_with_timeout",
        side_effect=concurrent.futures.TimeoutError,
    ):
        ok = retrieve_with_day_fallback(
            client, "some-dataset", _build_request, dest, timeout_s=5, search_days=3, label="test"
        )

    assert ok is False
    assert not os.path.exists(dest)


def test_retrieve_with_day_fallback_gives_up_after_search_days_exhausted(tmp_path):
    client = MagicMock()
    client.retrieve.side_effect = RuntimeError("400 Client Error")
    dest = str(tmp_path / "data" / "cached.nc")

    ok = retrieve_with_day_fallback(
        client, "some-dataset", _build_request, dest, timeout_s=5, search_days=3, label="test"
    )

    assert ok is False
    assert client.retrieve.call_count == 3
    assert not os.path.exists(dest)
