#!/usr/bin/env python3
"""CDSAPI_KEY gate for the greenhouse_gases layer: same shape as every other
RULE__missing_* case (AIS/OpenWeather/FIRMS), disabling the whole section when the
key is missing. Both Absolute and Anomaly modes are sourced from CAMS via the CDS
API, so there's no keyless mode to preserve -- unlike the original design (GEOS-CF
supplied a keyless Absolute mode), dropped after live testing found GEOS-CF doesn't
serve CO2 at all; see the published spec's issue comments. See
routes/config.py::_build_config_data()/update_config().
"""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from atmos_gl.api import app
from atmos_gl.lib.config import AtmosGLConfig

client = TestClient(app)


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
        tmp_path, {"greenhouse_gases": {"enabled": True, "mode": "absolute"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["greenhouse_gases"]
    assert data["RULE__missing_cdsapi_key"] is True
    assert data["enabled"] is False


def test_get_config_does_not_flag_or_disable_when_key_present(tmp_path, monkeypatch):
    monkeypatch.setenv("CDSAPI_KEY", "some-token")
    patcher, _ = _with_temp_config(
        tmp_path, {"greenhouse_gases": {"enabled": True, "mode": "anomaly"}}
    )
    with patcher:
        resp = client.get("/api/config")

    data = resp.json()["data"]["greenhouse_gases"]
    assert "RULE__missing_cdsapi_key" not in data
    assert data["enabled"] is True


def test_update_config_strips_missing_cdsapi_key_rule_before_saving(tmp_path):
    patcher, tmp_config = _with_temp_config(
        tmp_path, {"greenhouse_gases": {"enabled": True, "mode": "absolute"}}
    )
    with patcher:
        resp = client.post(
            "/api/config",
            json={
                "greenhouse_gases": {
                    "enabled": True,
                    "mode": "absolute",
                    "RULE__missing_cdsapi_key": True,
                }
            },
        )

    assert resp.status_code == 200
    saved = json.loads(tmp_config.read_text())
    assert "RULE__missing_cdsapi_key" not in saved["greenhouse_gases"]
