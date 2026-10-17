#!/usr/bin/env python3
"""Config-UI validation for the UV Index layer (see test_config_field_specs.py for the
validator's own contract) and its exposure through GET /api/config."""

import json
from pathlib import Path
from unittest.mock import patch

from terrascope.lib.config import TerrascopeConfig
from terrascope.routes.field_specs import (
    FIELD_SPECS,
    section_label,
    validate_against_specs,
)

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation_and_ship_disabled():
    template = json.loads(TEMPLATE.read_text())

    assert validate_against_specs({"uv_index": template["uv_index"]}) == []
    section = template["uv_index"]
    assert section["enabled"] is False
    assert section["opacity"] == 70


def test_enabled_and_opacity_are_per_viewer_settings():
    assert FIELD_SPECS[("uv_index", "enabled")].personalizable
    assert FIELD_SPECS[("uv_index", "opacity")].personalizable


def test_opacity_must_be_a_percentage():
    assert validate_against_specs({"uv_index": {"opacity": 101}}) != []
    assert validate_against_specs({"uv_index": {"opacity": 60}}) == []


def test_the_section_is_titled_by_its_layer_name_not_the_raw_config_key():
    assert section_label("uv_index") == "UV Index"


def test_api_config_serves_the_who_bands_the_frontend_draws_its_legend_from(
    client, tmp_path
):
    cfg = tmp_path / "terrascope.json"
    cfg.write_text(json.dumps({"common": {}}))
    with patch(
        "terrascope.routes.config.load_config", return_value=TerrascopeConfig(str(cfg))
    ):
        spec = client.get("/api/config").json()["data"]["scalar_field_specs"][
            "uv_index"
        ]
    assert spec["title"] == "UV Index"
    assert [(b["from"], b["name"], b["color"]) for b in spec["bands"]] == [
        (0.0, "Low", "#4eb400"),
        (3.0, "Moderate", "#f7e400"),
        (6.0, "High", "#f85900"),
        (8.0, "Very high", "#d8001d"),
        (11.0, "Extreme", "#6b49c8"),
    ]
    assert spec["visible_from"] == 1.0
