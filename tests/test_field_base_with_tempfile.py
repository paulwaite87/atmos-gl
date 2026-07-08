#!/usr/bin/env python3
"""Tests for field_base.with_tempfile() -- the tempfile-lifecycle primitive extracted
from the 6 duplicated tempfile-write/unpack/cleanup blocks across gfs_atmos.py,
gfs_waves.py, and rtofs_currents.py's collect()/backfill_hour() pairs (architecture
review candidate "collapse the field-collector download -> unpack -> store mechanic").
"""
import glob
import os

import pytest

from worldmap.collectors.field_base import with_tempfile


def test_writes_data_and_yields_a_readable_path():
    with with_tempfile(b"hello grib", ".grib2") as tmp_path:
        assert tmp_path.endswith(".grib2")
        with open(tmp_path, "rb") as f:
            assert f.read() == b"hello grib"


def test_removes_the_tempfile_after_the_with_block():
    with with_tempfile(b"data", ".nc") as tmp_path:
        pass
    assert not os.path.exists(tmp_path)


def test_sweeps_idx_sidecars_when_cleanup_idx_true():
    with with_tempfile(b"data", ".grib2", cleanup_idx=True) as tmp_path:
        sidecar = tmp_path + ".923a.idx"
        open(sidecar, "w").close()
    assert not os.path.exists(tmp_path)
    assert glob.glob(tmp_path + "*.idx") == []


def test_leaves_idx_sidecars_when_cleanup_idx_false():
    with with_tempfile(b"data", ".nc") as tmp_path:
        sidecar = tmp_path + ".idx"
        open(sidecar, "w").close()
    assert not os.path.exists(tmp_path)
    assert os.path.exists(sidecar)
    os.remove(sidecar)  # this one's on us to clean up, not with_tempfile's job


def test_cleans_up_even_when_the_body_raises():
    with pytest.raises(ValueError):
        with with_tempfile(b"data", ".grib2") as tmp_path:
            raise ValueError("boom")
    assert not os.path.exists(tmp_path)
