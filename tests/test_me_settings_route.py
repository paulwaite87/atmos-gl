#!/usr/bin/env python3
"""Route-level tests for the personal settings mechanism (issue #305/#314):
GET /me/settings, PATCH /api/me/settings, DELETE /api/me/settings/{section}, and
GET /api/config's merge of a signed-in user's overrides on top of the global defaults.
"""
import json
from unittest.mock import patch

from atmos_gl.api import app
from atmos_gl.db.user_settings_adapter import FakeUserSettingsAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.routes.auth import get_user_adapter
from atmos_gl.routes.config import get_user_settings_adapter as config_get_user_settings_adapter
from atmos_gl.routes.me_settings import get_user_settings_adapter as me_get_user_settings_adapter
from tests.conftest import make_signed_in_session


def _sign_in(client, settings_adapter: FakeUserSettingsAdapter) -> int:
    """Signs `client` in as a fresh non-admin user, wiring `settings_adapter` in for
    both routes/config.py's and routes/me_settings.py's OWN get_user_settings_adapter
    factory (two separate DI targets, same repo convention as status.py/
    system_status.py each defining their own get_process_status_adapter). Returns the
    new user's id."""
    fake, token = make_signed_in_session(is_admin=False)
    app.dependency_overrides[get_user_adapter] = lambda: fake
    app.dependency_overrides[config_get_user_settings_adapter] = lambda: settings_adapter
    app.dependency_overrides[me_get_user_settings_adapter] = lambda: settings_adapter
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return next(iter(fake._users))


def _with_temp_config(tmp_path, initial: dict, *, target="atmos_gl.routes.config.load_config"):
    """routes/config.py and routes/me_settings.py each import load_config directly
    (`from atmos_gl.routes.config import load_config`), so patch.object binds it at
    each importer's own name -- `target` picks which one a given test needs patched."""
    tmp_config = tmp_path / "atmos-gl.json"
    tmp_config.write_text(json.dumps(initial))
    return patch(target, return_value=AtmosGLConfig(str(tmp_config)))


def _with_temp_config_for_me_settings(tmp_path, initial: dict):
    return _with_temp_config(tmp_path, initial, target="atmos_gl.routes.me_settings.load_config")


# --- GET /me/settings (the page) ---


def test_me_settings_page_requires_sign_in(client):
    resp = client.get("/me/settings")
    assert resp.status_code == 401


def test_me_settings_page_renders_only_personalizable_precipitation_fields(client, tmp_path):
    _sign_in(client, FakeUserSettingsAdapter())
    with _with_temp_config_for_me_settings(
        tmp_path,
        {"precipitation": {"opacity": 60, "palette": "standard", "min_mm_hr": 0.5, "level_of_detail": "2"}},
    ):
        resp = client.get("/me/settings")

    assert resp.status_code == 200
    # Personalizable keys show up ...
    assert 'id="precipitation__opacity"' in resp.text
    assert 'id="precipitation__palette"' in resp.text
    assert 'id="precipitation__min_mm_hr"' in resp.text
    # ... a non-personalizable key in the same section does not.
    assert 'id="precipitation__level_of_detail"' not in resp.text


def test_me_settings_page_shows_the_users_own_override_not_the_global_value(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)
    fake_settings.merge_section(user_id, "precipitation", {"opacity": 85})

    with _with_temp_config_for_me_settings(
        tmp_path, {"precipitation": {"opacity": 60, "palette": "standard"}}
    ):
        resp = client.get("/me/settings")

    # The overridden 85 wins over the global 60.
    assert 'value="85' in resp.text
    assert 'value="60' not in resp.text


# --- PATCH /api/me/settings ---


def test_patch_requires_sign_in(client):
    resp = client.patch("/api/me/settings", json={"precipitation": {"opacity": 70}})
    assert resp.status_code == 401


def test_patch_merges_a_personalizable_field_and_is_reflected_on_reread(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)

    with _with_temp_config_for_me_settings(
        tmp_path, {"precipitation": {"opacity": 60, "palette": "standard", "min_mm_hr": 0.5}}
    ):
        resp = client.patch("/api/me/settings", json={"precipitation": {"opacity": 85}})

    assert resp.status_code == 200
    assert fake_settings.get_overrides(user_id) == {"precipitation": {"opacity": 85}}


def test_patch_rejects_a_non_personalizable_key(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    _sign_in(client, fake_settings)

    with _with_temp_config_for_me_settings(tmp_path, {"precipitation": {"level_of_detail": "2"}}):
        resp = client.patch("/api/me/settings", json={"precipitation": {"level_of_detail": "3"}})

    assert resp.status_code == 422


def test_patch_rejects_an_out_of_range_value(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    _sign_in(client, fake_settings)

    with _with_temp_config_for_me_settings(tmp_path, {"precipitation": {"opacity": 60}}):
        resp = client.patch("/api/me/settings", json={"precipitation": {"opacity": 999}})

    assert resp.status_code == 422


def test_patch_on_one_section_does_not_clobber_a_previously_saved_section(client, tmp_path):
    """The exact near-miss #313's implementation hit and fixed with POST /api/config's
    whole-tree replace semantics -- PATCH must never wipe another section's overrides.
    Only Precipitation has any personalizable keys yet (#315 curates the rest), so a
    second section's pre-existing override is seeded directly via the adapter here,
    standing in for "some other section this user already personalized"."""
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)
    fake_settings.merge_section(user_id, "sst", {"opacity": 20})

    with _with_temp_config_for_me_settings(tmp_path, {"precipitation": {"opacity": 60}}):
        client.patch("/api/me/settings", json={"precipitation": {"opacity": 85}})

    assert fake_settings.get_overrides(user_id) == {
        "precipitation": {"opacity": 85},
        "sst": {"opacity": 20},
    }


# --- DELETE /api/me/settings/{section} ---


def test_delete_requires_sign_in(client):
    resp = client.delete("/api/me/settings/precipitation")
    assert resp.status_code == 401


def test_delete_clears_the_users_override_for_that_section(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)

    with _with_temp_config_for_me_settings(tmp_path, {"precipitation": {"opacity": 60}}):
        client.patch("/api/me/settings", json={"precipitation": {"opacity": 85}})
        resp = client.delete("/api/me/settings/precipitation")

    assert resp.status_code == 200
    assert fake_settings.get_overrides(user_id) == {}


# --- GET /api/config merges a signed-in user's overrides ---


def test_get_config_is_unaffected_for_an_anonymous_visitor(client, tmp_path):
    with _with_temp_config(tmp_path, {"precipitation": {"opacity": 60, "palette": "standard"}}):
        resp = client.get("/api/config")

    data = resp.json()["data"]["precipitation"]
    assert data["opacity"] == 60
    assert data["palette"] == "standard"


def test_get_config_merges_the_signed_in_users_override(client, tmp_path):
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)
    fake_settings.merge_section(user_id, "precipitation", {"opacity": 85})

    with _with_temp_config(tmp_path, {"precipitation": {"opacity": 60, "palette": "standard"}}):
        resp = client.get("/api/config")

    data = resp.json()["data"]["precipitation"]
    assert data["opacity"] == 85
    # Untouched key keeps the global value.
    assert data["palette"] == "standard"


def test_get_config_never_lets_a_stale_override_of_a_non_personalizable_key_leak_through(client, tmp_path):
    """A key stored as an override that isn't (or is no longer) personalizable must
    never reach the response -- routes/config.py's _merge_personal_overrides re-checks
    FIELD_SPECS.personalizable at read time, not just at write time. Writes directly
    via the adapter (bypassing PATCH's own validation) to simulate exactly that stale
    state -- a key that used to be personalizable and no longer is."""
    fake_settings = FakeUserSettingsAdapter()
    user_id = _sign_in(client, fake_settings)
    fake_settings.merge_section(user_id, "precipitation", {"level_of_detail": "3"})

    with _with_temp_config(tmp_path, {"precipitation": {"opacity": 60, "level_of_detail": "2"}}):
        resp = client.get("/api/config")

    assert resp.json()["data"]["precipitation"]["level_of_detail"] == "2"
