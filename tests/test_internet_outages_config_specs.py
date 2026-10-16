#!/usr/bin/env python3
"""Config-UI validation for the Internet Outages layer's settings: the field specs must
accept what the shipped template defaults to and reject values the layer could not act on
(see test_config_field_specs.py for the validator's own contract)."""

import json
from pathlib import Path

from terrascope.routes.field_specs import section_label, validate_against_specs

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation_and_ship_disabled():
    template = json.loads(TEMPLATE.read_text())

    errors = validate_against_specs(
        {
            "internet_outages": template["internet_outages"],
            "housekeeper": template["housekeeper"],
        }
    )

    assert errors == []
    section = template["internet_outages"]
    assert section["enabled"] is False
    assert section["grace_days"] == 1
    assert section["lookback_days"] == 3
    assert section["min_duration_minutes"] == 30
    assert section["runs_per_day"] == 96
    assert template["housekeeper"]["internet_outages_expiry_days"] == 7
    assert "internet_outages" in template["data_collector"]["datasources"]
    assert template["data_collector"]["channel_enabled"]["internet_outages"] is True


def test_the_grace_period_must_be_within_the_housekeepers_retention_range():
    assert validate_against_specs({"internet_outages": {"grace_days": 0}}) != []
    assert validate_against_specs({"internet_outages": {"grace_days": 30}}) != []
    assert validate_against_specs({"internet_outages": {"grace_days": 3}}) == []


def test_the_minimum_duration_must_be_a_sensible_number_of_minutes():
    assert (
        validate_against_specs({"internet_outages": {"min_duration_minutes": -5}}) != []
    )
    assert (
        validate_against_specs({"internet_outages": {"min_duration_minutes": 100000}})
        != []
    )
    assert (
        validate_against_specs({"internet_outages": {"min_duration_minutes": 45}}) == []
    )


def test_the_section_is_titled_by_its_layer_name_not_the_raw_config_key():
    assert section_label("internet_outages") == "Internet Outages"
