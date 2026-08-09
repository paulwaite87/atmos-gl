#!/usr/bin/env python3
"""GET /api/config is public and unauthenticated (issue #304 explicitly keeps it that
way -- the map depends on it to load), but _inject_secrets() (lib/config.py) stamps
real AIS_API_KEY/OPENWEATHER_API_KEY/FIRMS_API_KEY values into shipping_collector/
lightning_collector/fires so those collectors keep working. Those three must never
reach this public response; common.api_key (MAPTILER_API_KEY) is the one deliberate
exception -- the map embeds it directly in client-side MapTiler tile requests, so
it's already public by design. See routes/config.py::_strip_backend_only_secrets().
"""
import json
from unittest.mock import patch

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.routes.config import _strip_backend_only_secrets


def test_strip_backend_only_secrets_drops_api_key_from_non_common_sections():
    data = {
        "shipping_collector": {"enabled": True, "api_key": "ais-secret"},
        "lightning_collector": {"enabled": True, "api_key": "owm-secret"},
        "fires": {"enabled": True, "api_key": "firms-secret"},
        "common": {"api_key": "maptiler-public-key"},
    }

    stripped = _strip_backend_only_secrets(data)

    assert "api_key" not in stripped["shipping_collector"]
    assert "api_key" not in stripped["lightning_collector"]
    assert "api_key" not in stripped["fires"]
    assert stripped["common"]["api_key"] == "maptiler-public-key"


def test_strip_backend_only_secrets_does_not_mutate_the_input():
    data = {"fires": {"api_key": "firms-secret"}}

    _strip_backend_only_secrets(data)

    assert data["fires"]["api_key"] == "firms-secret"


def test_strip_backend_only_secrets_leaves_sections_without_an_api_key_untouched():
    data = {"quakes": {"enabled": True, "min_mag": 4.0}}

    stripped = _strip_backend_only_secrets(data)

    assert stripped == data


def test_get_config_omits_backend_collector_api_keys(client, tmp_path, monkeypatch):
    monkeypatch.setenv("AIS_API_KEY", "ais-secret")
    monkeypatch.setenv("OPENWEATHER_API_KEY", "owm-secret")
    monkeypatch.setenv("FIRMS_API_KEY", "firms-secret")
    monkeypatch.setenv("MAPTILER_API_KEY", "maptiler-public-key")

    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text(json.dumps({
        "common": {},
        "shipping_collector": {"enabled": True},
        "lightning_collector": {"enabled": True},
        "fires": {"enabled": True},
    }))

    with patch(
        "atmos_gl.routes.config.load_config",
        return_value=AtmosGLConfig(str(tmp_config)),
    ):
        resp = client.get("/api/config")

    data = resp.json()["data"]
    assert "api_key" not in data["shipping_collector"]
    assert "api_key" not in data["lightning_collector"]
    assert "api_key" not in data["fires"]
    assert data["common"]["api_key"] == "maptiler-public-key"
