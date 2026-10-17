#!/usr/bin/env python3
"""Tests for GET /api/config's `scalar_field_specs` field (architecture review
candidate, second round: "Give the Scalar field spec one source of truth").

temperature.js/ozone.js/stormwatch.js/pwat.js used to hand-copy their own VMIN/VMAX/
TICKS/title, enforced only by a "mirrors SPECS[...]" comment -- these tests lock
_build_config_data()'s new scalar_field_specs field as the single value those four
modules now read instead, sourced directly from tasks/scalar_field.py's SPECS.
"""
import json
from unittest.mock import patch

from terrascope.lib.config import TerrascopeConfig
from terrascope.tasks.scalar_field import SPECS


def test_get_config_exposes_scalar_field_specs_for_every_field(client, tmp_path):
    tmp_config = tmp_path / "terrascope.json"
    tmp_config.write_text(json.dumps({"common": {}}))

    with patch(
        "terrascope.routes.config.load_config",
        return_value=TerrascopeConfig(str(tmp_config)),
    ):
        resp = client.get("/api/config")

    specs = resp.json()["data"]["scalar_field_specs"]
    assert set(specs) == {"temperature", "ozone", "uv_index", "stormwatch", "pwat"}


def test_get_config_scalar_field_specs_match_the_backend_spec_exactly(client, tmp_path):
    tmp_config = tmp_path / "terrascope.json"
    tmp_config.write_text(json.dumps({"common": {}}))

    with patch(
        "terrascope.routes.config.load_config",
        return_value=TerrascopeConfig(str(tmp_config)),
    ):
        resp = client.get("/api/config")

    specs = resp.json()["data"]["scalar_field_specs"]
    for name, spec in SPECS.items():
        expected = {"vmin": spec.vmin, "vmax": spec.vmax, "ticks": spec.ticks, "title": spec.title}
        if spec.bands:  # banded specs (uv_index) add bands + visible_from -- tested in test_uv_index_config_specs
            expected["bands"] = specs[name]["bands"]
            expected["visible_from"] = spec.visible_from
        assert specs[name] == expected
