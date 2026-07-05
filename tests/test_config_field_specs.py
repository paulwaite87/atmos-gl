#!/usr/bin/env python3
"""Tests for the schema-driven "Global" tab of the config UI (architecture review
candidate "htmx for the configuration UI", vertical slice). FIELD_SPECS/field_label/
format_slider_badge/validate_against_specs in routes/field_specs.py replace the
~46-branch option-name dispatch in the legacy ui/config/index.html JS; these tests
lock the pure spec functions and the two routes (GET /config, POST /api/config) that
consume them.
"""
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from worldmap.api import app
from worldmap.lib.config import WorldMapConfig
from worldmap.routes.field_specs import (
    SliderSpec,
    FIELD_SPECS,
    field_label,
    format_slider_badge,
    clamp_slider_value,
    validate_against_specs,
)

client = TestClient(app)


# --- Pure spec helpers ---


def test_field_label_uses_override_for_animation_fields():
    assert field_label("animation", "stepping_rate") == "Forecast stepping rate"
    assert (
        field_label("animation", "forecast_stepping")
        == "Forecast stepping (hourly playback)"
    )


def test_field_label_falls_back_to_spaced_capitalised():
    assert field_label("common", "auto_rotate_speed") == "Auto rotate speed"


def test_format_slider_badge_raw_when_no_decimals():
    spec = SliderSpec(min=0, max=100, step=1)
    assert format_slider_badge(spec, 45) == "45"


def test_format_slider_badge_applies_decimals_and_suffix():
    spec = SliderSpec(min=-90, max=90, step=1, decimals=1, suffix=" deg")
    assert format_slider_badge(spec, 12.345) == "12.3 deg"


def test_clamp_slider_value_passes_through_in_range_values():
    spec = SliderSpec(min=-90, max=90, step=1)
    assert clamp_slider_value(spec, 45) == 45


def test_clamp_slider_value_clamps_a_stale_out_of_range_value():
    """Guards the badge/slider-position mismatch that a stored value outside the
    (now-corrected) range would otherwise cause -- e.g. a starting_latitude left
    over from before the swapped-range bug was fixed."""
    spec = SliderSpec(min=-90, max=90, step=1)
    assert clamp_slider_value(spec, 165) == 90
    assert clamp_slider_value(spec, -165) == -90


def test_starting_lat_lon_ranges_are_geographically_correct():
    """Regression guard for the swapped min/max bug in the legacy JS (latitude got
    +/-180, longitude got +/-90)."""
    lat = FIELD_SPECS[("common", "starting_latitude")]
    lon = FIELD_SPECS[("common", "starting_longitude")]
    assert (lat.min, lat.max) == (-90.0, 90.0)
    assert (lon.min, lon.max) == (-180.0, 180.0)


def test_validate_against_specs_accepts_in_range_slider():
    assert validate_against_specs({"common": {"auto_rotate_speed": 0.5}}) == []


def test_validate_against_specs_rejects_out_of_range_slider():
    errors = validate_against_specs({"common": {"auto_rotate_speed": 99}})
    assert len(errors) == 1
    assert "auto_rotate_speed" in errors[0]


def test_validate_against_specs_rejects_unknown_select_option():
    errors = validate_against_specs({"common": {"basemap": "not-a-real-style"}})
    assert len(errors) == 1


def test_validate_against_specs_ignores_fields_without_a_spec():
    """Fields with no FIELD_SPECS entry stay permissive, matching legacy behaviour --
    both for genuinely generic fields and for tabs not yet migrated."""
    assert validate_against_specs({"common": {"workdir": "literally anything"}}) == []


def test_validate_against_specs_ignores_missing_sections():
    assert validate_against_specs({"quakes": {"min_mag": 4.5}}) == []


# --- GET /config: renders the schema-driven Global tab ---


def test_config_page_renders_slider_bounds_and_fixed_lat_lon_ranges():
    resp = client.get("/config")
    assert resp.status_code == 200
    html = resp.text
    assert 'min="0.01"' in html and 'max="1.0"' in html  # auto_rotate_speed
    assert 'min="-90.0"' in html  # starting_latitude, fixed
    assert 'min="-180.0"' in html  # starting_longitude, fixed


def test_config_page_renders_select_options_with_current_value_selected():
    resp = client.get("/config")
    html = resp.text
    assert '<option value="satellite"' in html
    assert "selected" in html


def test_config_page_renders_toggle_as_checkbox():
    resp = client.get("/config")
    html = resp.text
    assert 'id="common__atmosphere"' in html
    assert 'type="checkbox"' in html


def test_config_page_falls_back_to_text_input_for_unspecced_field():
    """workdir has no FIELD_SPECS entry -- must still render via the generic fallback."""
    resp = client.get("/config")
    html = resp.text
    assert 'id="common__workdir"' in html


# --- POST /api/config: spec-based validation ---


def test_update_config_rejects_out_of_range_slider():
    resp = client.post("/api/config", json={"common": {"auto_rotate_speed": 99}})
    assert resp.status_code == 422


def test_update_config_rejects_invalid_select_option():
    resp = client.post("/api/config", json={"common": {"basemap": "not-a-real-style"}})
    assert resp.status_code == 422


def test_update_config_accepts_valid_payload(tmp_path):
    """Uses a throwaway config file so this test can't corrupt config/worldmap.json."""
    tmp_config = tmp_path / "worldmap.json"
    tmp_config.write_text('{"common": {"auto_rotate_speed": 0.5}}')

    with patch(
        "worldmap.routes.config.load_config",
        return_value=WorldMapConfig(str(tmp_config)),
    ):
        resp = client.post("/api/config", json={"common": {"auto_rotate_speed": 0.5}})

    assert resp.status_code == 200
    assert json.loads(tmp_config.read_text())["common"]["auto_rotate_speed"] == 0.5
