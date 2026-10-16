#!/usr/bin/env python3
"""The World Data section: its Control Center tab and Show-tab group, its /me settings
group, and Country Statistics' settings (indicator options from the catalog, the
max_age_years cutoff)."""
import json
from unittest.mock import patch

from terrascope.lib.config import TerrascopeConfig
from terrascope.lib.country_stats import INDICATORS
from terrascope.routes.field_specs import validate_against_specs
from tests.test_me_settings_route import _sign_in
from terrascope.db.user_settings_adapter import FakeUserSettingsAdapter

_SECTIONS = {
    "country_stats": {"enabled": False, "opacity": 70, "start_indicator": "population",
                      "show_people": True, "show_economy": True, "show_environment": True,
                      "max_age_years": 5, "runs_per_day": 1},
    "population_density": {"enabled": False, "opacity": 70, "palette": "thermal",
                           "min_density": 10, "max_density": 5000, "runs_per_day": 1},
    "landmass": {"enabled": False, "opacity": 90},
}


def _config_page(admin_client, tmp_path, monkeypatch):
    path = tmp_path / "terrascope.json"
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


def test_starting_indicator_offers_none_and_the_whole_catalog(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    select = _between(html, 'id="country_stats__start_indicator"', "</select>")
    assert 'value="none"' in select
    for indicator in INDICATORS:
        assert f'value="{indicator.id}"' in select
    assert select.count("<option") == len(INDICATORS) + 1


def test_the_picker_groups_can_each_be_switched_off(admin_client, tmp_path, monkeypatch):
    html = _config_page(admin_client, tmp_path, monkeypatch)
    for group in ("people", "economy", "environment"):
        assert f'id="country_stats__show_{group}"' in html
    assert 'id="country_stats__indicator"' not in html


def test_settings_are_validated():
    assert validate_against_specs({"country_stats": {
        "start_indicator": "net_migration_rate", "show_economy": False, "max_age_years": 10}}) == []
    assert validate_against_specs({"country_stats": {"start_indicator": "none"}}) == []
    assert validate_against_specs({"country_stats": {"start_indicator": "nope"}})
    assert validate_against_specs({"country_stats": {"show_people": "yes"}})
    assert validate_against_specs({"country_stats": {"max_age_years": 99}})


def test_the_globes_config_data_carries_the_catalog(client):
    groups = client.get("/api/config").json()["data"]["country_stats_catalog"]
    assert [g["id"] for g in groups] == ["people", "economy", "environment"]
    assert [i["id"] for g in groups for i in g["indicators"]] == [i.id for i in INDICATORS]


def test_me_settings_groups_world_data(client, tmp_path):
    _sign_in(client, FakeUserSettingsAdapter())
    path = tmp_path / "terrascope.json"
    path.write_text(json.dumps(_SECTIONS))
    with patch("terrascope.routes.me_settings.load_config", return_value=TerrascopeConfig(str(path))):
        html = client.get("/me/settings").text
    assert "World Data" in html
    assert 'id="country_stats__start_indicator"' in html
    assert 'id="country_stats__show_people"' in html
    assert 'id="country_stats__max_age_years"' in html
