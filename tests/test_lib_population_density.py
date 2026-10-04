#!/usr/bin/env python3
"""lib/population_density.py: the chunked GHSL download and the people -> density
aggregation, against small synthetic GeoTIFFs and a fake Range-serving fetch -- no
network."""
import os
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from atmos_gl.lib import population_density as pd


def _write_geotiff(path, data, west, north, res, nodata=-200.0):
    with rasterio.open(
        path, "w", driver="GTiff", height=data.shape[0], width=data.shape[1], count=1,
        dtype="float32", crs="EPSG:4326", transform=from_origin(west, north, res, res),
        nodata=nodata,
    ) as dst:
        dst.write(data.astype(np.float32), 1)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    return tmp_path / "home"


def test_cell_areas_sum_to_the_earths_surface():
    lat, lon = pd.build_grid(1.0)
    total = pd.cell_areas_km2(lat, 1.0).sum() * len(lon)
    assert total == pytest.approx(4 * np.pi * 6371.0072 ** 2, rel=1e-9)


def test_build_grid_is_north_first_cell_centres():
    lat, lon = pd.build_grid(10.0)
    assert lat[0] == 85.0 and lat[-1] == -85.0
    assert lon[0] == -175.0 and lon[-1] == 175.0


def test_aggregate_people_sums_source_pixels_into_grid_cells(tmp_path):
    src = np.ones((180, 360), dtype=np.float32)
    src[0, 0] = -200.0  # nodata counts as nobody
    path = tmp_path / "pop.tif"
    _write_geotiff(path, src, -180.0, 90.0, 1.0)

    people, lat, lon = pd.aggregate_people(str(path), step_deg=10.0)

    assert people.shape == (18, 36)
    assert people[0, 0] == 99.0
    assert people[1:, :].min() == 100.0 and people[0, 1:].min() == 100.0
    assert people.sum() == 180 * 360 - 1


def test_aggregate_people_handles_a_source_not_aligned_to_the_grid(tmp_path):
    # GHSL's own origin sits a fraction of a pixel off -180/90; each source pixel
    # still lands in exactly one cell, by its centre.
    src = np.ones((170, 360), dtype=np.float32)
    path = tmp_path / "pop.tif"
    _write_geotiff(path, src, -180.004, 85.004, 1.0)

    people, _lat, _lon = pd.aggregate_people(str(path), step_deg=10.0)

    assert people.sum() == 170 * 360


def test_density_from_zip_divides_people_by_cell_area(tmp_path):
    src = np.zeros((180, 360), dtype=np.float32)
    src[80:90, 170:180] = 1000.0  # one 10deg cell just north-west of (0, 0)
    tif = tmp_path / "pop.tif"
    _write_geotiff(tif, src, -180.0, 90.0, 1.0)
    zip_path = tmp_path / "pop.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(tif, "GHS_POP_test.tif")

    density, lat, _lon = pd.density_from_zip(str(zip_path), step_deg=10.0)

    area = pd.cell_areas_km2(lat, 10.0)[8]
    assert density[8, 17] == pytest.approx(100_000 / area, rel=1e-6)
    assert np.count_nonzero(density) == 1


def test_density_grid_round_trips(tmp_path):
    density = np.arange(6, dtype=np.float32).reshape(2, 3)
    path = tmp_path / "data" / "grid.npz"
    pd.save_density_grid(str(path), density, np.array([45.0, -45.0]), np.array([-120.0, 0.0, 120.0]))
    got, lat, lon = pd.load_density_grid(str(path))
    assert np.array_equal(got, density) and list(lat) == [45.0, -45.0] and len(lon) == 3


# ---- chunked download ----------------------------------------------------------


_PAYLOAD = bytes(range(256)) * 40  # 10240 bytes


def _remote():
    return {"url": "https://example.test/pop.zip", "size": len(_PAYLOAD), "marker": '"etag-1"'}


def _fake_fetch(fail=()):
    calls = []

    def fetch(url, part_dir, index, start, end, timeout):
        calls.append(index)
        if index in fail:
            raise IOError("boom")
        with open(pd._chunk_path(part_dir, index), "wb") as f:
            f.write(_PAYLOAD[start:end + 1])

    return fetch, calls


def test_download_chunks_fetches_every_chunk_and_assembles_the_file(home, monkeypatch):
    monkeypatch.setattr(pd, "CHUNK_BYTES", 1000)
    fetch, calls = _fake_fetch()
    monkeypatch.setattr(pd, "_fetch_chunk", fetch)

    assert pd.download_chunks(_remote(), budget_s=60, workers=3)

    assert sorted(calls) == list(range(11))
    with open(pd.assemble_zip(_remote()), "rb") as f:
        assert f.read() == _PAYLOAD


def test_download_chunks_resumes_only_the_missing_chunks(home, monkeypatch):
    monkeypatch.setattr(pd, "CHUNK_BYTES", 1000)
    fetch, _ = _fake_fetch(fail={3, 7})
    monkeypatch.setattr(pd, "_fetch_chunk", fetch)
    assert not pd.download_chunks(_remote(), budget_s=60, workers=3)
    assert pd.chunk_progress(_remote()) == (9, 11)

    fetch, calls = _fake_fetch()
    monkeypatch.setattr(pd, "_fetch_chunk", fetch)
    assert pd.download_chunks(_remote(), budget_s=60, workers=3)
    assert sorted(calls) == [3, 7]


def test_download_chunks_starts_nothing_once_the_budget_is_spent(home, monkeypatch):
    monkeypatch.setattr(pd, "CHUNK_BYTES", 1000)
    fetch, calls = _fake_fetch()
    monkeypatch.setattr(pd, "_fetch_chunk", fetch)

    assert not pd.download_chunks(_remote(), budget_s=0, workers=3)
    assert calls == []


def test_a_republished_file_downloads_into_its_own_dir(home):
    other = {**_remote(), "marker": '"etag-2"'}
    assert pd.part_dir_for(_remote()) != pd.part_dir_for(other)


def test_is_current_needs_the_same_url_and_marker_and_a_grid(tmp_path):
    workdir = str(tmp_path)
    remote = _remote()
    assert not pd.is_current(workdir, remote)

    pd.save_cached_source(workdir, remote)
    assert not pd.is_current(workdir, remote)  # no grid yet

    os.makedirs(tmp_path / "data", exist_ok=True)
    open(pd.density_grid_cache_path(workdir), "wb").close()
    assert pd.is_current(workdir, remote)
    assert not pd.is_current(workdir, {**remote, "marker": '"etag-2"'})
    assert not pd.is_current(workdir, {**remote, "url": "https://example.test/new.zip"})
