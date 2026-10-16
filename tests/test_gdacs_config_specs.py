#!/usr/bin/env python3
"""Config-UI validation for the GDACS Alerts layer's settings: the field specs must
accept what the shipped template defaults to and reject values the collector could not
act on (see test_config_field_specs.py for the validator's own contract)."""
import json
from pathlib import Path

from terrascope.routes.field_specs import section_label, validate_against_specs

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation():
    template = json.loads(TEMPLATE.read_text())

    errors = validate_against_specs(
        {"gdacs": template["gdacs"], "housekeeper": template["housekeeper"]}
    )

    assert errors == []


def test_an_unknown_minimum_alert_level_is_rejected():
    assert validate_against_specs({"gdacs": {"min_alert": "Purple"}}) != []
    assert validate_against_specs({"gdacs": {"min_alert": "Red"}}) == []


def test_an_unknown_hazard_type_is_rejected():
    assert validate_against_specs({"gdacs": {"types": ["EQ", "XX"]}}) != []
    assert validate_against_specs({"gdacs": {"types": ["EQ", "FL"]}}) == []


def test_the_grace_period_must_be_within_the_housekeepers_retention_range():
    assert validate_against_specs({"gdacs": {"grace_days": 0}}) != []
    assert validate_against_specs({"gdacs": {"grace_days": 30}}) != []
    assert validate_against_specs({"gdacs": {"grace_days": 7}}) == []


def test_the_section_is_titled_by_its_layer_name_not_the_raw_config_key():
    assert section_label("gdacs") == "GDACS Alerts"
