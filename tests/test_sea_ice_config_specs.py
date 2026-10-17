#!/usr/bin/env python3
"""Config-UI validation for the Sea Ice layer (see test_config_field_specs.py for the
validator's own contract) and its data source on the Data Status page."""

import json
from pathlib import Path

from terrascope.collectors import CACHE_COLLECTORS
from terrascope.collectors.sea_ice import SeaIceCollector
from terrascope.routes.field_specs import FIELD_SPECS, section_label, validate_against_specs

TEMPLATE = Path(__file__).parent.parent / "config" / "terrascope.json.tmpl"


def test_the_shipped_template_defaults_pass_validation_and_ship_disabled():
    template = json.loads(TEMPLATE.read_text())

    assert validate_against_specs({"sea_ice": template["sea_ice"]}) == []
    section = template["sea_ice"]
    assert section["enabled"] is False
    assert section["opacity"] == 70
    assert section["runs_per_day"] == 2  # the file changes about daily, like SST


def test_the_oisst_endpoint_is_a_listed_data_source_the_collector_is_gated_by():
    template = json.loads(TEMPLATE.read_text())
    collector = template["data_collector"]

    assert collector["datasources"]["sea_ice"] == (
        "https://downloads.psl.noaa.gov/Datasets/noaa.oisst.v2.highres"
    )
    assert collector["channel_enabled"]["sea_ice"] is True
    assert SeaIceCollector in CACHE_COLLECTORS


def test_enabled_and_opacity_are_per_viewer_settings():
    assert FIELD_SPECS[("sea_ice", "enabled")].personalizable
    assert FIELD_SPECS[("sea_ice", "opacity")].personalizable


def test_opacity_must_be_a_percentage():
    assert validate_against_specs({"sea_ice": {"opacity": 101}}) != []
    assert validate_against_specs({"sea_ice": {"opacity": 60}}) == []


def test_the_section_is_titled_by_its_layer_name():
    assert section_label("sea_ice") == "Sea Ice"
