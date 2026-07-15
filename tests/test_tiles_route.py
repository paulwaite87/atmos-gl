#!/usr/bin/env python3
"""Route-level tests for GET /api/tiles/{layer}/meta and .../{z}/{x}/{y}.png
(architecture review candidate "give raster_tiles the seam the other routers have").
No other router in this codebase actually Depends()-seams AtmosGLConfig (they all
load it inline) -- this introduces that pattern here specifically, since tiles.py's
only real dependency is config, not a DB adapter. Previously untested entirely.

Uses a synthetic TileSpec registered under raster_tiles.SPECS for the duration of each
test (monkeypatched), rather than relying on a real registered layer -- SPECS is empty
as of the waves->createFillLayer migration (waves was its only entry; see
tasks/waves.py), and this route's behavior is generic across whatever IS registered,
not specific to waves.
"""
import pytest

from atmos_gl.routes.tiles import get_config
from atmos_gl.tiles import raster_tiles as rt
from atmos_gl.api import app


class FakeConfig:
    """Minimal stand-in for AtmosGLConfig -- only the methods raster_tiles.py
    actually calls (get_section, get_setting)."""

    def __init__(self, sections=None):
        self._sections = sections or {}

    def get_section(self, section):
        return self._sections.get(section, {})

    def get_setting(self, section, setting, default=None):
        return self._sections.get(section, {}).get(setting, default)


@pytest.fixture
def registered_test_layer(monkeypatch):
    """Register a synthetic TileSpec under 'test_layer' for the duration of a test."""
    spec = rt.TileSpec(section="test_layer", vmin=0.0, vmax=1.0)
    monkeypatch.setattr(rt, "SPECS", {"test_layer": spec})
    return "test_layer"


def test_meta_for_unknown_layer_is_404(client, tmp_path, registered_test_layer):
    app.dependency_overrides[get_config] = lambda: FakeConfig(
        {"common": {"workdir": str(tmp_path)}}
    )

    resp = client.get("/api/tiles/nonexistent/meta")

    assert resp.status_code == 404


def test_meta_for_known_but_unpublished_layer(client, tmp_path, registered_test_layer):
    app.dependency_overrides[get_config] = lambda: FakeConfig(
        {"common": {"workdir": str(tmp_path)}}
    )

    resp = client.get(f"/api/tiles/{registered_test_layer}/meta")

    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["available"] is False
    assert body["version"] is None
    assert body["minzoom"] == 0


def test_tile_out_of_zoom_range_is_404(client, tmp_path, registered_test_layer):
    app.dependency_overrides[get_config] = lambda: FakeConfig(
        {"common": {"workdir": str(tmp_path)}}
    )

    resp = client.get(f"/api/tiles/{registered_test_layer}/99/0/0.png")

    assert resp.status_code == 404
    assert "zoom" in resp.json()["detail"]


def test_tile_out_of_xy_range_is_404(client, tmp_path, registered_test_layer):
    app.dependency_overrides[get_config] = lambda: FakeConfig(
        {"common": {"workdir": str(tmp_path)}}
    )

    # z=2 -> valid x,y in [0,4); 4 is out of range
    resp = client.get(f"/api/tiles/{registered_test_layer}/2/4/0.png")

    assert resp.status_code == 404
    assert "range" in resp.json()["detail"]


def test_tile_for_unpublished_layer_is_404(client, tmp_path, registered_test_layer):
    app.dependency_overrides[get_config] = lambda: FakeConfig(
        {"common": {"workdir": str(tmp_path)}}
    )

    resp = client.get(f"/api/tiles/{registered_test_layer}/0/0/0.png")

    assert resp.status_code == 404
    assert "empty" in resp.json()["detail"]
