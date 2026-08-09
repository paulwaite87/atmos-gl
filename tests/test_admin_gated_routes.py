#!/usr/bin/env python3
"""Route-level tests for require_admin (routes/auth.py), the admin gate issue #304
puts in front of the global-configuration and internal-status surface. One
representative case per gated endpoint proves the gate is actually wired in (no
cookie -> 401, a signed-in non-admin -> 403, a signed-in admin -> not blocked); the
endpoints' own business logic is exercised elsewhere (test_config_field_specs.py,
test_status_route.py, test_system_status_route.py). GET /api/config staying public is
the one explicit "don't regress" acceptance criterion from #304 -- the map itself
depends on it to load for anonymous visitors -- so it gets its own guard below rather
than living only implicitly by omission.

Every route that reads or writes config (all of routes/config.py, plus
routes/status.py's two POST endpoints) is patched to a throwaway tmp_path config
file, NEVER the real config/atmos-gl.json -- these are route-level tests exercised
via the real app, and load_config()/config.save() do real file I/O against
CONFIG_PATH with no adapter seam to fake, unlike the DB-backed routes below.
"""
import json
from unittest.mock import patch

import pytest

from atmos_gl.api import app
from atmos_gl.db.process_status_adapter import FakeProcessStatusAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.routes.auth import get_user_adapter
from atmos_gl.routes.status import (
    get_collector_classes,
    get_cache_collector_classes,
    get_field_collector_classes,
    get_embeddable_collector_classes,
    get_task_classes,
    get_process_status_adapter as status_get_process_status_adapter,
)
from atmos_gl.routes.system_status import (
    get_process_status_adapter as system_status_get_process_status_adapter,
)
from tests.conftest import make_signed_in_session

# (method, path) for every route gated by Depends(require_admin).
GATED_ROUTES = [
    ("get", "/config"),
    ("get", "/config/section_defaults/common"),
    ("post", "/api/config"),
    ("get", "/api/data_status"),
    ("post", "/api/data_status/channel_enabled/quakes"),
    ("post", "/api/data_status/runs_per_day/quakes"),
    ("get", "/api/system_status"),
]


def _request(client, method, path):
    if method == "get":
        return client.get(path)
    if path == "/api/data_status/channel_enabled/quakes":
        return client.post(path, json={"enabled": True})
    if path == "/api/data_status/runs_per_day/quakes":
        return client.post(path, json={"runs_per_day": 6})
    return client.post(path, json={"common": {}})


def _sign_in(client, *, is_admin: bool) -> None:
    fake, token = make_signed_in_session(is_admin=is_admin)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    client.cookies.set(SESSION_COOKIE_NAME, token)


def _stub_out_infra(tmp_path):
    """Everything the "admin admitted" case needs so each route can run its real body
    against a throwaway config file and empty/fake registries, never real Postgres or
    the real config/atmos-gl.json. Returns the list of started patchers (caller stops
    them)."""
    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text(json.dumps({"common": {}}))
    cfg = AtmosGLConfig(str(tmp_config))

    patchers = [
        patch("atmos_gl.routes.config.load_config", return_value=cfg),
        patch("atmos_gl.routes.status.load_config", return_value=cfg),
        patch("atmos_gl.routes.system_status.load_config", return_value=cfg),
    ]
    for p in patchers:
        p.start()

    app.dependency_overrides[get_collector_classes] = lambda: []
    app.dependency_overrides[get_cache_collector_classes] = lambda: []
    app.dependency_overrides[get_field_collector_classes] = lambda: []
    app.dependency_overrides[get_embeddable_collector_classes] = lambda: []
    app.dependency_overrides[get_task_classes] = lambda: {}
    app.dependency_overrides[status_get_process_status_adapter] = FakeProcessStatusAdapter
    app.dependency_overrides[system_status_get_process_status_adapter] = FakeProcessStatusAdapter

    return patchers


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_gated_route_rejects_an_unauthenticated_request(client, method, path):
    resp = _request(client, method, path)
    assert resp.status_code == 401


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_gated_route_rejects_a_signed_in_non_admin(client, method, path):
    _sign_in(client, is_admin=False)
    resp = _request(client, method, path)
    assert resp.status_code == 403


@pytest.mark.parametrize("method,path", GATED_ROUTES)
def test_gated_route_admits_a_signed_in_admin(client, method, path, tmp_path):
    patchers = _stub_out_infra(tmp_path)
    try:
        _sign_in(client, is_admin=True)
        resp = _request(client, method, path)
    finally:
        for p in patchers:
            p.stop()
    assert resp.status_code == 200


def test_public_config_endpoint_stays_unauthenticated(client, tmp_path):
    """The one explicit "must not regress" acceptance criterion: the map's own
    bootstrap and timeline_boot.js's live poll both depend on this staying public.
    Patched to a throwaway config, not the real config/atmos-gl.json (which may not
    exist at all on a fresh checkout -- see docs/conventions/settings.md)."""
    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text(json.dumps({"common": {}}))
    with patch("atmos_gl.routes.config.load_config", return_value=AtmosGLConfig(str(tmp_config))):
        resp = client.get("/api/config")
    assert resp.status_code == 200
