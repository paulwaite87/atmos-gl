#!/usr/bin/env python3
"""GLOFAS_API_KEY gate for the flood_risk layer -- unlike greenhouse_gases/
air_quality's single-key-disables-whole-section shape, this gate is MODE-SPECIFIC:
only Live mode (GloFAS forecast via EWDS) needs a credential; Historical mode (JRC
hazard maps, static/no-auth) needs none, so it must stay enabled/available even
without GLOFAS_API_KEY configured. See issue #371, routes/config.py::
_build_config_data()/update_config().
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


def test_get_config_disables_live_mode_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("GLOFAS_API_KEY", raising=False)
    patcher, _ = _with_temp_config(
        tmp_path, {"flood_risk": {"enabled": True, "mode": "live"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["flood_risk"]
    assert data["RULE__missing_glofas_apikey"] is True
    assert data["enabled"] is False


def test_get_config_does_not_flag_or_disable_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("GLOFAS_API_KEY", "some-token")
    patcher, _ = _with_temp_config(
        tmp_path, {"flood_risk": {"enabled": True, "mode": "live"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["flood_risk"]
    assert "RULE__missing_glofas_apikey" not in data
    assert data["enabled"] is True


def test_get_config_historical_mode_stays_enabled_without_a_key(tmp_path, monkeypatch):
    """Historical mode (JRC hazard maps) needs no credential at all -- must not be
    disabled just because GLOFAS_API_KEY (a Live-mode-only requirement) is unset."""
    monkeypatch.delenv("GLOFAS_API_KEY", raising=False)
    patcher, _ = _with_temp_config(
        tmp_path, {"flood_risk": {"enabled": True, "mode": "historical"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["flood_risk"]
    assert "RULE__missing_glofas_apikey" not in data
    assert data["enabled"] is True


def test_update_config_strips_missing_glofas_apikey_rule_before_saving(tmp_path):
    patcher, tmp_config = _with_temp_config(
        tmp_path, {"flood_risk": {"enabled": True, "mode": "live"}}
    )
    with patcher:
        resp = client.post(
            "/api/config",
            json={
                "flood_risk": {
                    "enabled": True,
                    "mode": "live",
                    "RULE__missing_glofas_apikey": True,
                }
            },
        )

    assert resp.status_code == 200
    saved = json.loads(tmp_config.read_text())
    assert "RULE__missing_glofas_apikey" not in saved["flood_risk"]
