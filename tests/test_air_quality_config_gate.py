#!/usr/bin/env python3
"""CDSAPI_KEY gate for the air_quality layer: same shape as the greenhouse_gases
layer's equivalent gate (mirrors tests/test_greenhouse_gases_config_gate.py) --
same CDS/ADS source family, so a missing key disables the whole section the same way.
See routes/config.py::_build_config_data()/update_config().
"""
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from atmos_gl.api import app
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.db.user_settings_adapter import FakeUserSettingsAdapter
from atmos_gl.routes.auth import get_user_adapter
from atmos_gl.routes.config import get_user_settings_adapter
from tests.conftest import make_signed_in_session

client = TestClient(app)


# POST /api/config is gated by require_admin (issue #304); see
# tests/test_config_field_specs.py's identical fixture for why this is autouse +
# re-applied per test rather than a one-time module-level assignment. GET /api/config
# now also reads a user_settings adapter for any signed-in caller (issue #305/#314,
# routes/config.py's get_config) -- faked here too so an admin session set up purely
# to satisfy require_admin elsewhere in this file doesn't make GET /api/config reach
# for a real database connection this test suite has none of.
@pytest.fixture(autouse=True)
def _admin_session():
    fake, token = make_signed_in_session(is_admin=True)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    app.dependency_overrides[get_user_settings_adapter] = lambda: FakeUserSettingsAdapter()
    client.cookies.set(SESSION_COOKIE_NAME, token)


def _with_temp_config(tmp_path, initial: dict):
    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text(json.dumps(initial))
    return patch(
        "atmos_gl.routes.config.load_config",
        return_value=AtmosGLConfig(str(tmp_config)),
    ), tmp_config


def test_get_config_disables_the_section_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("CDSAPI_KEY", raising=False)
    patcher, _ = _with_temp_config(
        tmp_path, {"air_quality": {"enabled": True, "variable": "pm2_5"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["air_quality"]
    assert data["RULE__missing_cdsapi_key"] is True
    assert data["enabled"] is False


def test_get_config_does_not_flag_or_disable_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CDSAPI_KEY", "some-token")
    patcher, _ = _with_temp_config(
        tmp_path, {"air_quality": {"enabled": True, "variable": "pm10"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["air_quality"]
    assert "RULE__missing_cdsapi_key" not in data
    assert data["enabled"] is True


def test_update_config_strips_missing_cdsapi_key_rule_before_saving(tmp_path):
    patcher, tmp_config = _with_temp_config(
        tmp_path, {"air_quality": {"enabled": True, "variable": "pm2_5"}}
    )
    with patcher:
        resp = client.post(
            "/api/config",
            json={
                "air_quality": {
                    "enabled": True,
                    "variable": "pm2_5",
                    "RULE__missing_cdsapi_key": True,
                }
            },
        )

    assert resp.status_code == 200
    saved = json.loads(tmp_config.read_text())
    assert "RULE__missing_cdsapi_key" not in saved["air_quality"]
