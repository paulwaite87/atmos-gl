#!/usr/bin/env python3
"""_retrieve_and_unzip: shared submit-then-poll-then-unzip mechanics behind both
CDS-backed greenhouse-gas collectors (CamsGhgForecastCollector,
CamsEgg4BaselineCollector) -- both CDS datasets deliver data_format=netcdf_zip (a zip
archive), not a raw netCDF, so the fetch isn't complete until the archive's .nc member
is extracted to the real cache path.
"""
import os
from unittest.mock import MagicMock

import pytest

from atmos_gl.collectors.greenhouse_gases import _retrieve_and_unzip


def test_retrieve_and_unzip_extracts_the_nc_member_to_cache_dest(tmp_path, make_netcdf_zip_bytes):
    zip_bytes = make_netcdf_zip_bytes("some_download_name.nc", b"real-netcdf-bytes")

    def fake_retrieve(dataset, request, target):
        with open(target, "wb") as f:
            f.write(zip_bytes)

    client = MagicMock()
    client.retrieve.side_effect = fake_retrieve
    dest = str(tmp_path / "data" / "cached.nc")

    _retrieve_and_unzip(client, "some-dataset", {}, dest, timeout_s=5, label="test")

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
        _retrieve_and_unzip(client, "some-dataset", {}, dest, timeout_s=5, label="test")

    assert not os.path.exists(dest)
