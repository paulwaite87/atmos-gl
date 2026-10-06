#!/usr/bin/env python3
"""PopulationDensityCollector (collectors/population_density.py) and
PopulationDensityUpdater (tasks/population_density.py), with the download and
aggregation mocked -- lib/population_density.py's own tests cover those."""
import os
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from atmos_gl.collectors import population_density as mod
from atmos_gl.collectors.population_density import PopulationDensityCollector
from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter
from atmos_gl.lib.population_density import (
    GHSL_POP_PAGE_URL,
    GHSL_POP_URL,
    cached_source,
    density_grid_cache_path,
    save_density_grid,
)
from atmos_gl.tasks.population_density import ENCODE_DOMAIN, PopulationDensityUpdater, log_density

_REMOTE = {"url": GHSL_POP_URL, "size": 1000, "marker": '"etag-1"'}


def make_collector(workdir, datasource=""):
    c = PopulationDensityCollector.__new__(PopulationDensityCollector)
    c.settings = {}

    def fake_get_setting(section, key, default=None):
        if section == "common" and key == "workdir":
            return workdir
        return default

    c.config = MagicMock()
    c.config.get_setting.side_effect = fake_get_setting
    c.datasource_url = MagicMock(return_value=datasource)
    c.process_status_adapter = FakeProcessStatusAdapter()
    return c


def _grid():
    return np.ones((2, 3), dtype=np.float32), np.array([45.0, -45.0]), np.array([-120.0, 0.0, 120.0])


def test_source_url_is_the_dataset_page_not_the_zip(tmp_path):
    assert make_collector(str(tmp_path)).source_url() == GHSL_POP_PAGE_URL


def test_the_configured_datasource_overrides_the_default_url(tmp_path):
    assert make_collector(str(tmp_path))._url() == GHSL_POP_URL
    assert make_collector(str(tmp_path), "https://example.test/new.zip")._url() == "https://example.test/new.zip"


def test_has_new_data_is_false_when_the_head_fails(tmp_path):
    c = make_collector(str(tmp_path))
    with patch.object(mod, "remote_file_info", side_effect=IOError("down")):
        assert c.has_new_data() is False


def test_has_new_data_until_the_grid_matches_the_remote(tmp_path):
    c = make_collector(str(tmp_path))
    with patch.object(mod, "remote_file_info", return_value=_REMOTE):
        assert c.has_new_data() is True
        save_density_grid(density_grid_cache_path(str(tmp_path)), *_grid())
        mod.save_cached_source(str(tmp_path), _REMOTE)
        assert c.has_new_data() is False


def test_collect_stops_after_a_partial_download(tmp_path):
    c = make_collector(str(tmp_path))
    c._remote = _REMOTE
    with patch.object(mod, "download_chunks", return_value=False), \
         patch.object(mod, "chunk_progress", return_value=(3, 10)), \
         patch.object(mod, "density_from_zip") as build:
        c.collect()
    build.assert_not_called()
    assert not os.path.exists(density_grid_cache_path(str(tmp_path)))


def test_collect_builds_the_grid_and_discards_the_download(tmp_path):
    c = make_collector(str(tmp_path))
    c._remote = _REMOTE
    with patch.object(mod, "download_chunks", return_value=True), \
         patch.object(mod, "assemble_zip", return_value="/x/source.zip"), \
         patch.object(mod, "density_from_zip", return_value=_grid()), \
         patch.object(mod, "discard_download") as discard:
        c.collect()
    assert os.path.exists(density_grid_cache_path(str(tmp_path)))
    assert cached_source(str(tmp_path)) == {"url": GHSL_POP_URL, "marker": '"etag-1"'}
    discard.assert_called_once_with(_REMOTE)


def test_a_failed_build_discards_the_download_so_the_next_cycle_starts_over(tmp_path):
    c = make_collector(str(tmp_path))
    c._remote = _REMOTE
    with patch.object(mod, "download_chunks", return_value=True), \
         patch.object(mod, "assemble_zip", return_value="/x/source.zip"), \
         patch.object(mod, "density_from_zip", side_effect=ValueError("bad zip")), \
         patch.object(mod, "discard_download") as discard:
        try:
            c.collect()
        except ValueError:
            pass
    discard.assert_called_once_with(_REMOTE)
    assert cached_source(str(tmp_path)) is None


def test_is_stale_while_a_download_is_part_finished(tmp_path, monkeypatch):
    import time

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    c = make_collector(str(tmp_path))
    c.settings = {"runs_per_day": 1}
    assert c.is_stale(time.monotonic()) is False
    os.makedirs(tmp_path / "home" / ".local" / "share" / "population_density" / "abc")
    assert c.is_stale(time.monotonic()) is True


def test_collect_records_download_progress_for_the_data_status_page(tmp_path):
    c = make_collector(str(tmp_path))
    c._remote = _REMOTE

    def partial(remote, budget_s, on_progress=None):
        on_progress(4, 31)
        return False

    with patch.object(mod, "download_chunks", side_effect=partial), \
         patch.object(mod, "chunk_progress", return_value=(4, 31)):
        c.collect()

    status = c.data_status()
    assert round(status["percent"], 1) == round(100 * 4 / 31, 1)
    assert "4/31" in status["detail"]


def test_data_status_shows_the_build_once_downloaded_then_100_once_cached(tmp_path):
    c = make_collector(str(tmp_path))
    c._record_progress(31, 31)
    assert c.data_status()["percent"] == 99.0
    assert "building" in c.data_status()["detail"]

    save_density_grid(density_grid_cache_path(str(tmp_path)), *_grid())
    assert c.data_status()["percent"] == 100.0


def test_data_status_shows_the_next_check_even_with_the_layer_hidden(tmp_path):
    c = make_collector(str(tmp_path))
    c.settings = {"enabled": False, "runs_per_day": 1}
    assert c.data_status()["next_update"] is not None


# ---- PopulationDensityUpdater -------------------------------------------------


def test_log_density_is_nan_where_nobody_lives():
    got = log_density(np.array([0.0, 1.0, 1000.0], dtype=np.float32))
    # float32 log10 can land an ULP off (3.0000002) depending on the CPU's SIMD path.
    assert np.isnan(got[0]) and got[1:] == pytest.approx([0.0, 3.0], abs=1e-6)


def _bare_updater(workdir, output_path):
    u = PopulationDensityUpdater.__new__(PopulationDensityUpdater)
    u.workdir = workdir
    u.section = "population_density"
    u.output_path = output_path
    return u


def test_updater_does_nothing_before_the_grid_exists(tmp_path):
    out = tmp_path / "data" / "population_density.png"
    _bare_updater(str(tmp_path), str(out)).run()
    assert not out.exists()


def test_updater_encodes_log_density_with_empty_cells_transparent(tmp_path):
    density = np.array([[0.0, 10.0 ** ENCODE_DOMAIN[1]]], dtype=np.float32)
    save_density_grid(density_grid_cache_path(str(tmp_path)), density, np.array([0.0]), np.array([0.0, 1.0]))
    out = tmp_path / "data" / "population_density.png"

    _bare_updater(str(tmp_path), str(out)).run()

    px = np.asarray(Image.open(out))
    assert px[0, 0, 3] == 0                      # nobody: alpha 0
    assert tuple(px[0, 1]) == (255, 255, 0, 255)  # top of the domain: norm 1.0
