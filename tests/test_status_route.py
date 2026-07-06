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
"""
from datetime import datetime, timezone

from worldmap.routes.status import (
    get_collector_classes,
    get_cache_collector_classes,
    get_field_collector_classes,
    get_embeddable_collector_classes,
    get_task_classes,
)
from worldmap.api import app


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
