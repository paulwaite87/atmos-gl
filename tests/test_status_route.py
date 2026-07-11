#!/usr/bin/env python3
"""Route-level tests for GET /api/data_status (architecture review candidate "Give
routers the seam the Fakes are waiting for" -- the deferred status.py follow-on).

This route's real untestability was never the one FieldCatalogAdapter it constructs
(fieldstore.get_store freezes as a process-wide singleton on first use, so overriding
that adapter here wouldn't reliably reach it) -- it's the 23 real collector/task
classes across 5 registries it iterates. Injecting the registries lets these tests
substitute a handful of stub classes and exercise the route's OWN logic (iteration,
per-class exception swallowing, _serialize(), the final envelope) without touching a
real DB, config file, or the fieldstore singleton -- previously untestable at all.

POST /api/data_status/channel_enabled/{key} tests use a throwaway config file (via
CONFIG_PATH) rather than the real one, since this endpoint writes to disk.
"""
import json
from datetime import datetime, timezone

from atmos_gl.routes.status import (
    get_collector_classes,
    get_cache_collector_classes,
    get_field_collector_classes,
    get_embeddable_collector_classes,
    get_task_classes,
    _build_layer_channel_keys,
    _serialize,
    _display_name,
)
from atmos_gl.api import app


class _StubCollector:
    """Ignores whatever constructor args a real collector would need (config, or
    config+store, or config_path) -- proving the route's DI seam means a test never
    has to supply a real one."""

    def __init__(self, *args, **kwargs):
        pass

    def data_status(self):
        return {
            "name": "stub_collector",
            "kind": "collector",
            "percent": 100.0,
            "last_updated": datetime(2026, 6, 13, 18, 0, tzinfo=timezone.utc),
            "next_update": datetime(2026, 6, 13, 19, 0, tzinfo=timezone.utc),
            "enabled": True,
            "detail": None,
        }


class _StubGatedCollector:
    """A stub with channel_key set -- e.g. sst/quakes -- to prove the route reads and
    forwards it, and that a stub with NO channel_key attribute (_StubCollector, matching
    a real un-gated collector like markers) still serializes fine via getattr's
    default rather than raising."""

    channel_key = "stub_channel"

    def __init__(self, *args, **kwargs):
        pass

    def data_status(self):
        return {
            "name": "stub_gated_collector",
            "kind": "collector",
            "percent": 50.0,
            "last_updated": None,
            "next_update": None,
            "enabled": True,
            "detail": None,
        }


class _RaisingCollector:
    def __init__(self, *args, **kwargs):
        pass

    def data_status(self):
        raise RuntimeError("simulated collector failure")


class _StubTask:
    def __init__(self, *args, **kwargs):
        pass

    def layer_status(self):
        return {
            "name": "stub_layer",
            "kind": "layer",
            "percent": 75.0,
            "last_updated": None,
            "next_update": None,
            "enabled": True,
            "detail": None,
        }


def _override_all_empty():
    app.dependency_overrides[get_collector_classes] = lambda: ()
    app.dependency_overrides[get_cache_collector_classes] = lambda: ()
    app.dependency_overrides[get_field_collector_classes] = lambda: ()
    app.dependency_overrides[get_embeddable_collector_classes] = lambda: []
    app.dependency_overrides[get_task_classes] = lambda: {}


def test_data_status_with_all_empty_registries(client):
    _override_all_empty()

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "data": {"collectors": [], "layers": []}}


def test_data_status_reflects_stub_collector_and_task(client):
    _override_all_empty()
    app.dependency_overrides[get_collector_classes] = lambda: (_StubCollector,)
    app.dependency_overrides[get_task_classes] = lambda: {"stub": _StubTask}

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["data"]["collectors"]) == 1
    assert data["data"]["collectors"][0]["name"] == "stub_collector"
    assert data["data"]["collectors"][0]["last_updated"] == "2026-06-13T18:00:00+00:00"
    assert len(data["data"]["layers"]) == 1
    assert data["data"]["layers"][0]["name"] == "stub_layer"


def test_data_status_defaults_channel_key_to_none_when_collector_class_lacks_it(client):
    """A real un-gated collector (markers) has no channel_key attribute at all --
    must serialize with channel_key: null rather than raising."""
    _override_all_empty()
    app.dependency_overrides[get_collector_classes] = lambda: (_StubCollector,)

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    assert resp.json()["data"]["collectors"][0]["channel_key"] is None


def test_data_status_forwards_a_collector_classs_channel_key(client):
    _override_all_empty()
    app.dependency_overrides[get_collector_classes] = lambda: (_StubGatedCollector,)

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    assert resp.json()["data"]["collectors"][0]["channel_key"] == "stub_channel"


def test_data_status_forwards_a_layers_derived_channel_key(client):
    """isobars is a real TASK_CLASSES entry backed by gfs_atmos in production; here a
    stub field collector plays gfs_atmos's role to prove the layers loop actually
    looks the section up in the derived mapping, not just passes None through."""
    _override_all_empty()

    class _StubFieldCollector:
        channel_key = "stub_gfs"
        products = {"isobars": None}

        def __init__(self, *args, **kwargs):
            pass

        def data_status(self):
            return {
                "name": "stub_gfs", "kind": "collector", "percent": 0.0,
                "last_updated": None, "next_update": None, "enabled": True, "detail": None,
            }

    app.dependency_overrides[get_field_collector_classes] = lambda: (_StubFieldCollector,)
    app.dependency_overrides[get_task_classes] = lambda: {"isobars": _StubTask}

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    assert resp.json()["data"]["layers"][0]["channel_key"] == "stub_gfs"


def test_data_status_swallows_a_failing_collector_and_keeps_the_rest(client):
    _override_all_empty()
    app.dependency_overrides[get_collector_classes] = lambda: (_RaisingCollector, _StubCollector)

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    collectors = resp.json()["data"]["collectors"]
    assert len(collectors) == 1
    assert collectors[0]["name"] == "stub_collector"


def test_data_status_populates_every_registry_independently(client):
    app.dependency_overrides[get_collector_classes] = lambda: (_StubCollector,)
    app.dependency_overrides[get_cache_collector_classes] = lambda: (_StubCollector,)
    app.dependency_overrides[get_field_collector_classes] = lambda: (_StubCollector,)
    app.dependency_overrides[get_embeddable_collector_classes] = lambda: [_StubCollector]
    app.dependency_overrides[get_task_classes] = lambda: {"a": _StubTask, "b": _StubTask}

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["collectors"]) == 4  # one per registry
    assert len(data["layers"]) == 2


def test_build_layer_channel_keys_maps_every_field_collector_product():
    class _FakeGfsAtmos:
        channel_key = "gfs_atmos"
        products = {"isobars": None, "wind": None, "humidity": None}

    class _FakeGfsWaves:
        channel_key = "gfs_waves"
        products = {"waves": None}

    mapping = _build_layer_channel_keys((_FakeGfsAtmos, _FakeGfsWaves), ())

    assert mapping == {
        "isobars": "gfs_atmos",
        "wind": "gfs_atmos",
        "humidity": "gfs_atmos",
        "waves": "gfs_waves",
    }


def test_build_layer_channel_keys_maps_cache_collectors_by_section():
    class _FakeSst:
        channel_key = "sst"
        section = "sst"

    mapping = _build_layer_channel_keys((), (_FakeSst,))

    assert mapping == {"sst": "sst"}


def test_build_layer_channel_keys_skips_a_collector_with_no_channel_key():
    """markers isn't part of channel_enabled -- must not appear in the mapping at all
    (not even as None), since a `None` value would be indistinguishable from
    "channel_key wasn't set" if ever iterated rather than looked up by key."""
    class _FakeUngated:
        channel_key = None
        products = {"markers": None}
        section = "markers"

    field_mapping = _build_layer_channel_keys((_FakeUngated,), ())
    cache_mapping = _build_layer_channel_keys((), (_FakeUngated,))

    assert field_mapping == {}
    assert cache_mapping == {}


_BARE_STATUS = {
    "name": "x", "kind": "collector", "percent": 0.0,
    "last_updated": None, "next_update": None, "enabled": True, "detail": None,
}


def test_serialize_channel_on_is_none_when_not_gated():
    out = _serialize(_BARE_STATUS, None, {"quakes": False})
    assert out["channel_key"] is None
    assert out["channel_on"] is None


def test_serialize_channel_on_defaults_true_when_key_absent_from_dict():
    """A channel not yet present in channel_enabled (e.g. right after upgrading to
    this feature) must read as on, not off -- matches the collection-side gating's
    own default in _drive()/_collect_fields()."""
    out = _serialize(_BARE_STATUS, "quakes", {})
    assert out["channel_on"] is True


def test_serialize_channel_on_reflects_an_explicit_false():
    out = _serialize(_BARE_STATUS, "quakes", {"quakes": False})
    assert out["channel_on"] is False


def test_serialize_channel_on_reflects_an_explicit_true():
    out = _serialize(_BARE_STATUS, "quakes", {"quakes": True})
    assert out["channel_on"] is True


def _write_temp_config(tmp_path, data_collector_extra=None):
    path = tmp_path / "atmos-gl.json"
    config = {
        "common": {"workdir": "."},
        "data_collector": {"channel_enabled": {"quakes": True}, **(data_collector_extra or {})},
    }
    path.write_text(json.dumps(config))
    return path


def test_set_channel_enabled_persists_a_flip(client, tmp_path, monkeypatch):
    config_path = _write_temp_config(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    resp = client.post(
        "/api/data_status/channel_enabled/quakes", json={"enabled": False}
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "success", "channel_key": "quakes", "enabled": False}
    saved = json.loads(config_path.read_text())
    assert saved["data_collector"]["channel_enabled"]["quakes"] is False


def test_set_channel_enabled_creates_the_dict_if_missing(client, tmp_path, monkeypatch):
    config_path = _write_temp_config(tmp_path, data_collector_extra={})
    config_path.write_text(json.dumps({"common": {"workdir": "."}, "data_collector": {}}))
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    resp = client.post(
        "/api/data_status/channel_enabled/gfs_atmos", json={"enabled": False}
    )

    assert resp.status_code == 200
    saved = json.loads(config_path.read_text())
    assert saved["data_collector"]["channel_enabled"]["gfs_atmos"] is False


def test_set_channel_enabled_rejects_a_non_boolean(client, tmp_path, monkeypatch):
    config_path = _write_temp_config(tmp_path)
    monkeypatch.setenv("CONFIG_PATH", str(config_path))

    resp = client.post(
        "/api/data_status/channel_enabled/quakes", json={"enabled": "false"}
    )

    assert resp.status_code == 422


def test_display_name_matches_section_labels_for_a_real_section():
    """Matches the Show tab's wording exactly -- section_label()'s own contract."""
    assert _display_name("quakes") == "Earthquakes"
    assert _display_name("pwat") == "Precipitable Water"
    assert _display_name("satellites_collector") == "Satellites Collector"


def test_display_name_overrides_the_three_field_collector_status_names():
    """These aren't real config sections -- section_label()'s .title() fallback would
    give "Gfs Atmos"/"Rtofs Currents", not proper GFS/RTOFS acronyms."""
    assert _display_name("gfs_atmos") == "GFS Atmos"
    assert _display_name("gfs_waves") == "GFS Waves"
    assert _display_name("rtofs_currents") == "RTOFS Currents"


def test_display_name_falls_back_to_title_case_for_an_unknown_key():
    assert _display_name("totally_unknown_key") == "Totally Unknown Key"


def test_serialize_attaches_display_name_without_touching_name():
    out = _serialize(_BARE_STATUS, None, {})
    assert out["name"] == "x"
    assert out["display_name"] == "X"


def test_data_status_groups_collector_suffixed_rows_at_the_bottom(client):
    """satellites_collector/shipping_collector/lightning_collector run a *service*,
    not a data source -- they should sort after every real source regardless of
    registry definition order, with each group's own relative order preserved."""
    _override_all_empty()

    class _FakeQuakes(_StubCollector):
        def data_status(self):
            return {**super().data_status(), "name": "quakes"}

    class _FakeSatellitesCollector(_StubCollector):
        def data_status(self):
            return {**super().data_status(), "name": "satellites_collector"}

    class _FakeStorms(_StubCollector):
        def data_status(self):
            return {**super().data_status(), "name": "storms"}

    # Deliberately interleaved, matching how the real registries are actually ordered.
    app.dependency_overrides[get_collector_classes] = lambda: (
        _FakeQuakes, _FakeSatellitesCollector, _FakeStorms,
    )

    resp = client.get("/api/data_status")

    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["data"]["collectors"]]
    assert names == ["quakes", "storms", "satellites_collector"]
