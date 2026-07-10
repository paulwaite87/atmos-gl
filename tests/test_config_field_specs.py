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

from atmos_gl.api import app
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.routes.field_specs import (
    SliderSpec,
    FIELD_SPECS,
    field_label,
    section_label,
    format_slider_badge,
    clamp_slider_value,
    to_display_value,
    initial_color_render,
    is_long_or_url_field,
    is_api_key_field,
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


def test_validate_against_specs_accepts_int_value_against_string_select_options():
    """vei_min is stored/posted as an int (4) but SelectSpec options are declared as
    strings ("4") -- regression guard: a legitimate value must not be rejected."""
    assert validate_against_specs({"volcanoes": {"vei_min": 4}}) == []


def test_validate_against_specs_ignores_fields_without_a_spec():
    """Fields with no FIELD_SPECS entry stay permissive, matching legacy behaviour --
    both for genuinely generic fields and for tabs not yet migrated."""
    assert validate_against_specs({"common": {"workdir": "literally anything"}}) == []


def test_validate_against_specs_ignores_missing_sections():
    assert validate_against_specs({"quakes": {"min_mag": 4.5}}) == []


# --- Events / Misc / Shipping batch: prefix badges, shared shapes, new kinds ---


def test_field_label_generic_override_beats_section_specific_fallback():
    """"outfile" is checked before any (section, option) override or the generic
    spaced-capitalised fallback, matching the legacy JS's unconditional first check."""
    assert field_label("quakes", "outfile") == "Output file"
    assert field_label("volcanoes", "outfile") == "Output file"


def test_field_label_section_specific_override_for_quakes_min_mag():
    assert field_label("quakes", "min_mag") == "Minimum magnitude"


def test_format_slider_badge_applies_prefix():
    spec = FIELD_SPECS[("quakes", "min_mag")]
    assert format_slider_badge(spec, 4.5) == "M 4.5"


def test_shared_slider_shape_reused_across_sections():
    """icon_zoom/runs_per_day are the same widget shape everywhere they appear --
    registered once and shared, not re-declared per section."""
    assert FIELD_SPECS[("quakes", "icon_zoom")] is FIELD_SPECS[("shipping", "icon_zoom")]
    assert (
        FIELD_SPECS[("quakes", "runs_per_day")]
        is FIELD_SPECS[("markers", "runs_per_day")]
    )


def test_initial_color_render_resolves_named_color_to_hex():
    assert initial_color_render("Violet") == ("#ee82ee", "Violet")


def test_initial_color_render_passes_through_raw_hex():
    assert initial_color_render("#070b18") == ("#070b18", "#070b18")


def test_initial_color_render_defaults_empty_value_to_white():
    assert initial_color_render("") == ("#ffffff", "White")


def test_is_long_or_url_field_flags_url_named_options():
    assert is_long_or_url_field("url", "short") is True


def test_is_long_or_url_field_flags_long_values_regardless_of_name():
    assert is_long_or_url_field("outfile", "x" * 40) is True


def test_is_long_or_url_field_false_for_short_non_url_values():
    assert is_long_or_url_field("outfile", "data/quakes.json") is False


def test_is_api_key_field_matches_injected_secret_fields():
    assert is_api_key_field("api_key") is True
    assert is_api_key_field("min_mag") is False


def test_validate_against_specs_accepts_valid_multiselect_subset():
    assert validate_against_specs({"volcanoes": {"erupt_date_codes": ["D1", "D2"]}}) == []


def test_validate_against_specs_rejects_multiselect_with_unknown_option():
    errors = validate_against_specs({"volcanoes": {"erupt_date_codes": ["D1", "nope"]}})
    assert len(errors) == 1


def test_validate_against_specs_rejects_non_list_multiselect_value():
    errors = validate_against_specs({"volcanoes": {"erupt_date_codes": "D1"}})
    assert len(errors) == 1


# --- GET /config: Events / Misc / Shipping tabs render correctly ---


def test_config_page_renders_prefixed_slider_badge():
    resp = client.get("/config")
    assert 'id="badge-quakes__min_mag"' in resp.text


def test_config_page_selects_correct_option_despite_stored_int_vs_string_options():
    """vei_min is stored as an int (4) in config.json but SelectSpec options are
    strings ("4") -- regression guard for the type-mismatch bug this exposed."""
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="volcanoes__vei_min"')
    select_html = html[idx : idx + 800]
    assert '<option value="4" selected>' in select_html


def test_config_page_renders_multiselect_with_correct_options_checked():
    resp = client.get("/config")
    html = resp.text
    assert 'id="volcanoes__erupt_date_codes"' in html
    assert 'array-select' in html
    idx = html.index('id="volcanoes__erupt_date_codes"')
    select_html = html[idx : idx + 1500]
    assert '<option value="D1" selected>' in select_html


def test_config_page_renders_color_picker_with_resolved_hex():
    resp = client.get("/config")
    html = resp.text
    assert 'id="volcanoes__marker_color"' in html
    assert 'value="#ee82ee"' in html  # Violet


def test_config_page_renders_unstructured_color_for_terminator():
    """terminator.shade_color saves as raw hex, not a named colour -- must not carry
    the structured-color-name-picker class."""
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="terminator__shade_color"')
    input_html = html[max(0, idx - 300) : idx + 50]
    assert "structured-color-name-picker" not in input_html


def test_config_page_full_widths_a_url_field():
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="quakes__url"')
    preceding = html[max(0, idx - 400) : idx]
    assert "col-12" in preceding


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
    """Uses a throwaway config file so this test can't corrupt config/atmos-gl.json."""
    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text('{"common": {"auto_rotate_speed": 0.5}}')

    with patch(
        "atmos_gl.routes.config.load_config",
        return_value=AtmosGLConfig(str(tmp_config)),
    ):
        resp = client.post("/api/config", json={"common": {"auto_rotate_speed": 0.5}})

    assert resp.status_code == 200
    assert json.loads(tmp_config.read_text())["common"]["auto_rotate_speed"] == 0.5


# --- Atmospheric / Climate batch: whole-step int display, sentinel badges,
# byte<->percent transform, unspecced-boolean fallback, section-conditional selects ---


def test_clamp_slider_value_returns_int_for_whole_step_sliders():
    """Regression guard: clamp_slider_value used to always float()-coerce, so a
    whole-step slider's badge showed "12.0px" instead of "12px" for any value that
    wasn't exactly at a boundary (the earlier scalar-field-style bug this batch's
    live verification caught)."""
    spec = SliderSpec(min=6, max=24, step=1)
    result = clamp_slider_value(spec, 12)
    assert result == 12
    assert isinstance(result, int)


def test_clamp_slider_value_keeps_float_for_fractional_step_sliders():
    spec = SliderSpec(min=0, max=5, step=0.25)
    result = clamp_slider_value(spec, 0.5)
    assert result == 0.5
    assert isinstance(result, float)


def test_format_slider_badge_whole_step_has_no_decimal_point():
    spec = SliderSpec(min=0, max=5000, step=100, suffix="J/Kg")
    assert format_slider_badge(spec, clamp_slider_value(spec, 1200)) == "1200J/Kg"


def test_format_slider_badge_zero_label_overrides_normal_formatting():
    spec = SliderSpec(min=0, max=5, step=0.25, suffix=" m", zero_label="off")
    assert format_slider_badge(spec, 0) == "off"
    assert format_slider_badge(spec, 0.5) == "0.5 m"


def test_format_slider_badge_pluralizes_suffix_based_on_count():
    spec = FIELD_SPECS[("clouds", "cache_expiry_days")]
    assert format_slider_badge(spec, 0) == "keep forever"
    assert format_slider_badge(spec, 1) == "1 day"
    assert format_slider_badge(spec, 5) == "5 days"


def test_to_display_value_converts_byte_to_percent_for_clouds_threshold():
    spec = FIELD_SPECS[("clouds", "threshold")]
    assert to_display_value(spec, 168) == 66  # round((168/255)*100)


def test_to_display_value_is_a_noop_for_ordinary_sliders():
    spec = FIELD_SPECS[("quakes", "min_mag")]
    assert to_display_value(spec, 4.5) == 4.5


def test_validate_against_specs_uses_raw_max_for_byte_to_percent_field():
    """clouds.threshold is posted in raw byte space (0-255), not the displayed
    slider's 0-100 percent space -- validation must check against the byte range."""
    assert validate_against_specs({"clouds": {"threshold": 255}}) == []
    errors = validate_against_specs({"clouds": {"threshold": 256}})
    assert len(errors) == 1
    assert "255" in errors[0]  # bound reported is the raw max, not 100


def test_shared_constants_reused_across_many_sections():
    """_ALPHA, _LEVEL_OF_DETAIL etc. are declared once and referenced under every
    field that needs them -- not redeclared per section."""
    assert FIELD_SPECS[("isobars", "alpha")] is FIELD_SPECS[("wind", "alpha")]
    assert FIELD_SPECS[("isobars", "alpha")] is FIELD_SPECS[("sst", "alpha")]
    assert (
        FIELD_SPECS[("isobars", "level_of_detail")]
        is FIELD_SPECS[("stormwatch", "level_of_detail")]
    )
    assert (
        FIELD_SPECS[("wind", "particle_speed")]
        is FIELD_SPECS[("animation", "stepping_rate")]
    )


def test_section_conditional_palette_options_differ_per_section():
    """palette is one legacy branch keyed on section -- each section's option list
    must stay independent, not accidentally share one shared constant."""
    sst_values = {v for v, _ in FIELD_SPECS[("sst", "palette")].options}
    ozone_values = {v for v, _ in FIELD_SPECS[("ozone", "palette")].options}
    pwat_values = {v for v, _ in FIELD_SPECS[("pwat", "palette")].options}
    assert sst_values == {"thermal", "vivid", "deep", "ocean"}
    assert ozone_values == {"alert", "high_contrast"}
    assert pwat_values == {"standard", "atmospheric_river", "deep_teal"}
    assert sst_values.isdisjoint(ozone_values)
    assert sst_values.isdisjoint(pwat_values)


# --- GET /config: Atmospheric / Climate tabs render correctly ---


def test_config_page_renders_byte_to_percent_slider_with_extra_class():
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="clouds__threshold"')
    input_html = html[max(0, idx - 100) : idx + 200]
    assert 'max="100"' in input_html  # displayed range, not the raw 0-255
    assert "cloud-threshold-slider" in input_html


def test_config_page_renders_unspecced_boolean_as_toggle_not_number_fallback():
    """ozone.stormwatch has no FIELD_SPECS entry distinct from other booleans --
    still must render as a checkbox, not a broken type=number input with value=True."""
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="ozone__stormwatch"')
    input_html = html[max(0, idx - 50) : idx + 50]
    assert 'type="checkbox"' in input_html


def test_config_page_renders_prefixed_gamma_slider():
    resp = client.get("/config")
    assert 'id="badge-clouds__gamma"' in resp.text


# --- Background batch: final tab, shared log_level, datasources accordion,
# the fallback-section-X regression fix, and the dead legacy JS deletion ---


def test_shared_log_level_reused_across_common_and_collector_sections():
    assert FIELD_SPECS[("common", "log_level")] is FIELD_SPECS[("data_collector", "log_level")]
    assert (
        FIELD_SPECS[("shipping_collector", "log_level")]
        is FIELD_SPECS[("lightning_collector", "log_level")]
    )


def test_format_slider_badge_combines_prefix_and_pluralized_suffix():
    spec = FIELD_SPECS[("housekeeper", "days_between_runs")]
    assert format_slider_badge(spec, 1) == "every 1 day"
    assert format_slider_badge(spec, 5) == "every 5 days"


def test_housekeeper_enabled_has_an_explicit_spec():
    """Unlike every other section, housekeeper.enabled is NOT skipped by
    render_tab_group's generic 'enabled' filter -- it's a real, visible field."""
    assert FIELD_SPECS[("housekeeper", "enabled")].kind == "toggle"


def test_config_page_renders_housekeeper_enabled_as_a_visible_toggle():
    resp = client.get("/config")
    html = resp.text
    idx = html.index('id="housekeeper__enabled"')
    input_html = html[max(0, idx - 50) : idx + 50]
    assert 'type="checkbox"' in input_html


def test_config_page_renders_datasources_accordion_with_existing_entries():
    """data_collector.datasources deliberately has no FIELD_SPECS entry -- it's
    rendered by its own dedicated macro (render_datasources_accordion), mirroring
    the legacy buildDatasourcesHTML() JS function server-side."""
    resp = client.get("/config")
    html = resp.text
    assert 'id="datasources-accordion-data_collector"' in html
    idx = html.index('id="datasources-accordion-data_collector"')
    accordion_html = html[idx : idx + 3500]
    assert ">gfs<" in accordion_html
    assert ">currents<" in accordion_html
    assert "addDatasource('data_collector')" in html[idx : idx + 4500]


def test_config_page_renders_fallback_section_for_gated_layers():
    """Regression guard: render_tab_group previously never emitted the
    fallback-section-X div toggleSectionVisibility() depends on, so toggling a
    layer off in the Show tab silently stopped hiding its settings fields."""
    resp = client.get("/config")
    html = resp.text
    assert 'id="fallback-section-quakes"' in html
    assert "Layer Display Off" in html


def test_config_page_omits_fallback_section_for_exempt_sections():
    resp = client.get("/config")
    html = resp.text
    assert 'id="fallback-section-common"' not in html
    assert 'id="fallback-section-housekeeper"' not in html


def test_section_label_matches_the_show_tab_wording():
    assert section_label("pwat") == "Precipitable Water"
    assert section_label("sst") == "Sea Surface Temp"
    assert section_label("storms") == "Storm Track"
    assert section_label("temperature") == "Air Temperature"


def test_section_label_falls_back_to_title_case_for_sections_without_a_show_tab_entry():
    assert section_label("map_builder") == "Map Builder"
    assert section_label("animation") == "Animation"


def test_config_page_renders_friendly_section_headings_not_raw_bracket_keys():
    """The settings heading and the "enable it in the Show tab" fallback prompt both
    used to show the raw config key in brackets (e.g. "[pwat] Properties") -- both now
    use the same friendly name the Show tab itself uses for that layer's toggle."""
    resp = client.get("/config")
    html = resp.text
    assert "Precipitable Water Properties" in html
    assert "[pwat] Properties" not in html
    assert "Enable <strong>Earthquakes</strong> in the Show tab to edit." in html
    assert "[quakes]" not in html


def test_config_page_renders_pwat_as_a_plain_toggle_not_a_climate_radio():
    """pwat isn't mutually exclusive with the sst/currents/waves/temperature/ozone/
    stormwatch climate base layer -- it must get its own Show-tab checkbox (like
    precipitation), never a radio__pwat entry in the exclusive_climate group."""
    resp = client.get("/config")
    html = resp.text
    assert 'type="checkbox" id="pwat__enabled"' in html
    assert 'id="radio__pwat"' not in html


def test_config_page_renders_pwat_fields_section_and_gated_fallback():
    resp = client.get("/config")
    html = resp.text
    assert 'id="fields-section-pwat"' in html
    assert 'id="fallback-section-pwat"' in html
    assert 'id="pwat__critical_pwat"' in html
    assert 'id="pwat__palette"' in html


def test_config_page_has_no_remaining_legacy_dispatch_code():
    """TAB_GROUPS/renderTabContainers became fully dead once every tab migrated --
    this guards against either being silently reintroduced."""
    resp = client.get("/config")
    html = resp.text
    assert "TAB_GROUPS" not in html
    assert "renderTabContainers" not in html


def test_config_page_still_has_the_interactive_datasource_functions():
    """These stay -- they handle add/remove/rename after initial load, unrelated
    to the deleted TAB_GROUPS-driven dispatch."""
    resp = client.get("/config")
    html = resp.text
    for fn in ("addDatasource", "updateDatasourceName", "updateDatasourceUrl", "deleteDatasource"):
        assert f"function {fn}" in html
