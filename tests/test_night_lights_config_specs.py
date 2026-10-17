#!/usr/bin/env python3
"""Config-UI validation for the Night Lights layer's settings (see
test_config_field_specs.py for the validator's own contract)."""

import json
from pathlib import Path

from terrascope.routes.field_specs import (
    FIELD_SPECS,
    section_label,
    validate_against_specs,
)

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation_and_ship_disabled():
    template = json.loads(TEMPLATE.read_text())

    assert validate_against_specs({"night_lights": template["night_lights"]}) == []
    section = template["night_lights"]
    assert section["enabled"] is False
    assert section["runs_per_day"] == 4
    assert section["expiry_hours"] == 12
    assert section["noise_floor"] == 40
    assert "night_lights" in template["data_collector"]["datasources"]
    assert template["data_collector"]["channel_enabled"]["night_lights"] is True


def test_the_noise_floor_must_be_a_byte_level_below_saturation():
    assert validate_against_specs({"night_lights": {"noise_floor": -1}}) != []
    assert validate_against_specs({"night_lights": {"noise_floor": 255}}) != []
    assert validate_against_specs({"night_lights": {"noise_floor": 20}}) == []


def test_opacity_is_a_per_viewer_setting_but_the_noise_floor_is_shared():
    assert FIELD_SPECS[("night_lights", "opacity")].personalizable
    assert not FIELD_SPECS[("night_lights", "noise_floor")].personalizable


def test_the_section_is_titled_by_its_layer_name_not_the_raw_config_key():
    assert section_label("night_lights") == "Night Lights"
