#!/usr/bin/env python3
"""The World Data section: its Control Center tab and Show-tab group, its /me settings
group, and Country Statistics' settings (indicator options from the catalog, the
max_age_years cutoff)."""
import json
from unittest.mock import patch

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.country_stats import INDICATORS
from atmos_gl.routes.field_specs import validate_against_specs
from tests.test_me_settings_route import _sign_in
from atmos_gl.db.user_settings_adapter import FakeUserSettingsAdapter

_SECTIONS = {
    "country_stats": {"enabled": False, "opacity": 70, "indicator": "population",
                      "max_age_years": 5, "runs_per_day": 1},
    "population_density": {"enabled": False, "opacity": 70, "palette": "thermal",
                           "min_density": 10, "max_density": 5000, "runs_per_day": 1},
    "landmass": {"enabled": False, "opacity": 90},
}


def _config_page(admin_client, tmp_path, monkeypatch):
    path = tmp_path / "atmos-gl.json"
    path.write_text(json.dumps(_SECTIONS))
    monkeypatch.setenv("CONFIG_PATH", str(path))
    resp = admin_client.get("/config")
    assert resp.status_code == 200
    return resp.text


def _between(html, start, end):
    i = html.index(start)
    return html[i:html.index(end, i)]


def test_world_data_has_its_own_control_center_tab(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    assert 'data-bs-target="#world-data-pane"' in html
    pane = _between(html, 'id="world-data-pane"', 'id="misc-pane"')
    assert "Country Statistics Properties" in pane
    assert "Population Density Properties" in pane


def test_population_density_moved_out_of_miscellaneous(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    misc = _between(html, 'id="misc-pane"', 'id="background-pane"')
    assert "Population Density" not in misc
    assert "Landmass Outlines Properties" in misc


def test_world_data_show_tab_group_toggles_both_layers(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    group = _between(html, "<h5>World Data</h5>", "<h5>Miscellaneous</h5>")
    assert 'id="country_stats__enabled"' in group
    assert 'id="population_density__enabled"' in group
    assert html.count('id="population_density__enabled"') == 1


def test_indicator_options_come_from_the_catalog(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    select = _between(html, 'id="country_stats__indicator"', "</select>")
    for indicator in INDICATORS:
        assert f'value="{indicator.id}"' in select
        assert indicator.label in select


def test_indicator_and_cutoff_are_validated():
    assert validate_against_specs({"country_stats": {"indicator": "life_expectancy",
                                                     "max_age_years": 10}}) == []
    assert validate_against_specs({"country_stats": {"indicator": "nope"}})
    assert validate_against_specs({"country_stats": {"max_age_years": 99}})


def test_me_settings_groups_world_data(client, tmp_path):
    _sign_in(client, FakeUserSettingsAdapter())
    path = tmp_path / "atmos-gl.json"
    path.write_text(json.dumps(_SECTIONS))
    with patch("atmos_gl.routes.me_settings.load_config", return_value=AtmosGLConfig(str(path))):
        html = client.get("/me/settings").text
    assert "World Data" in html
    assert 'id="country_stats__indicator"' in html
    assert 'id="country_stats__max_age_years"' in html
