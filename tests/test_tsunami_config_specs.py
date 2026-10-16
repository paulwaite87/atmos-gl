#!/usr/bin/env python3
"""Config-UI validation for the Tsunami Warnings layer's settings: the field specs must
accept what the shipped template defaults to and reject values the layer could not act on
(see test_config_field_specs.py for the validator's own contract)."""

import json
from pathlib import Path

from terrascope.routes.field_specs import section_label, validate_against_specs

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation_and_ship_disabled():
    template = json.loads(TEMPLATE.read_text())

    errors = validate_against_specs(
        {"tsunami": template["tsunami"], "housekeeper": template["housekeeper"]}
    )

    assert errors == []
    assert template["tsunami"]["enabled"] is False
    assert template["tsunami"]["include_information"] is False
    assert template["tsunami"]["grace_days"] == 1
    assert template["tsunami"]["runs_per_day"] == 288
    assert template["housekeeper"]["tsunami_expiry_days"] == 7


def test_the_grace_period_must_be_within_the_housekeepers_retention_range():
    assert validate_against_specs({"tsunami": {"grace_days": 0}}) != []
    assert validate_against_specs({"tsunami": {"grace_days": 30}}) != []
    assert validate_against_specs({"tsunami": {"grace_days": 3}}) == []


def test_the_section_is_titled_by_its_layer_name_not_the_raw_config_key():
    assert section_label("tsunami") == "Tsunami Warnings"
