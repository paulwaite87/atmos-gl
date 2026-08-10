#!/usr/bin/env python3
"""Personal settings for signed-in users (issue #305/#314): a settings page scoped to
just the FIELD_SPECS entries flagged `personalizable=True`, backed by a per-user sparse
{section: {option: value}} override stored in user_settings and merged on top of the
site's global config by routes/config.py's GET /api/config. Anonymous visitors never
see this page or touch this table at all."""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

from atmos_gl.db.user_adapter import UserAdapter
from atmos_gl.db.user_settings_adapter import UserSettingsAdapter
from atmos_gl.lib.auth import SESSION_COOKIE_NAME
from atmos_gl.lib.rate_limit import RateLimiter
from atmos_gl.routes.auth import get_user_adapter, require_login
from atmos_gl.routes.config import load_config
from atmos_gl.routes.field_specs import (
    FIELD_SPECS,
    field_label,
    section_label,
    format_slider_badge,
    clamp_slider_value,
    to_display_value,
    initial_color_render,
    validate_against_specs,
)

router = APIRouter(prefix="/api/me", tags=["Personal Settings"])
ui_router = APIRouter(tags=["Personal Settings UI"])

templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")
templates.env.globals["field_specs"] = FIELD_SPECS
templates.env.globals["field_label"] = field_label
templates.env.globals["section_label"] = section_label
templates.env.globals["format_slider_badge"] = format_slider_badge
templates.env.globals["clamp_slider_value"] = clamp_slider_value
templates.env.globals["to_display_value"] = to_display_value
templates.env.globals["initial_color_render"] = initial_color_render


def get_user_settings_adapter() -> UserSettingsAdapter:
    return UserSettingsAdapter()


# Shared by every signed-in-only route below (issue #307) -- settings writes and
# account deletion draw from the same per-user budget, since both are self-service
# actions on the caller's own row. Keyed by user id (there's always a resolved session
# by this point, unlike the pre-auth /login/{provider} rate limiter in routes/auth.py).
# 30 requests / minute: comfortably covers the debounced-autosave bursts this page's
# own script already limits legitimate traffic to, only catches a runaway client or
# deliberate abuse.
_settings_rate_limiter = RateLimiter(max_requests=30, window_seconds=60)


def get_settings_rate_limiter() -> RateLimiter:
    return _settings_rate_limiter


def require_login_rate_limited(
    user: dict = Depends(require_login), limiter: RateLimiter = Depends(get_settings_rate_limiter),
) -> dict:
    limiter.enforce(str(user["id"]))
    return user


def _personalizable_sections(overrides: dict) -> dict:
    """{section: {option: effective_value}} for every FIELD_SPECS entry flagged
    personalizable=True that's actually present in the live config -- the user's
    stored override wins per-key when present, otherwise the current global value.
    Reads the raw stored config directly (not _build_config_data()'s RULE__-injected
    view) -- none of #314's personalizable keys are ever RULE__-affected, so there's
    nothing to reconcile between the two yet."""
    global_data = load_config().config
    sections: dict[str, dict] = {}
    for (section, option), spec in FIELD_SPECS.items():
        if not spec.personalizable:
            continue
        section_global = global_data.get(section)
        if not isinstance(section_global, dict) or option not in section_global:
            continue
        value = overrides.get(section, {}).get(option, section_global[option])
        sections.setdefault(section, {})[option] = value
    return sections


def _validate_personalizable_patch(payload: dict) -> list[str]:
    """Rejects any (section, option) that isn't flagged personalizable=True -- a
    user's own PATCH must never be able to touch a setting the site hasn't opted in
    for personal override, even if it's a perfectly valid FIELD_SPECS entry otherwise
    -- then defers all the actual value-shape/range checking to
    field_specs.validate_against_specs, the same per-kind dispatch every other
    FIELD_SPECS-backed write (POST /api/config) already goes through, rather than
    re-implementing it here."""
    errors = []
    for section, section_payload in payload.items():
        if not isinstance(section_payload, dict):
            errors.append(f"{section}: expected an object of settings")
            continue
        for option in section_payload:
            spec = FIELD_SPECS.get((section, option))
            if spec is None or not spec.personalizable:
                errors.append(f"{section}.{option}: not a personalizable setting")
    errors.extend(validate_against_specs(payload))
    return errors


@ui_router.get("/me/settings")
def me_settings_page(
    request: Request,
    user: dict = Depends(require_login),
    settings_adapter: UserSettingsAdapter = Depends(get_user_settings_adapter),
):
    overrides = settings_adapter.get_overrides(user["id"])
    return templates.TemplateResponse(
        request, "me_settings.html", {"sections": _personalizable_sections(overrides)}
    )


@router.patch("/settings")
async def update_my_settings(
    payload: dict,
    user: dict = Depends(require_login_rate_limited),
    settings_adapter: UserSettingsAdapter = Depends(get_user_settings_adapter),
):
    """Merges one (or more) section's changed keys into the caller's stored overrides.
    Each section in `payload` is merged independently (existing keys in that section
    not present in this request are left as-is; other sections are untouched) -- the
    debounced autosave in me_settings.html always posts exactly one section at a time,
    but nothing here assumes that."""
    errors = _validate_personalizable_patch(payload)
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    for section, values in payload.items():
        settings_adapter.merge_section(user["id"], section, values)
    return {"status": "success"}


@router.delete("/settings/{section}")
def reset_my_settings_section(
    section: str,
    user: dict = Depends(require_login_rate_limited),
    settings_adapter: UserSettingsAdapter = Depends(get_user_settings_adapter),
):
    settings_adapter.clear_section(user["id"], section)
    return {"status": "success"}


@router.delete("")
def delete_my_account(
    response: Response,
    user: dict = Depends(require_login_rate_limited),
    user_adapter: UserAdapter = Depends(get_user_adapter),
):
    """Permanently deletes the caller's own account (issue #307): a hard delete of
    the User row, which cascades to user_identities/user_sessions/user_settings via
    their existing ondelete="CASCADE" foreign keys (db/models.py) -- no manual
    cross-table cleanup needed here."""
    user_adapter.delete_user(user["id"])
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"status": "success"}
