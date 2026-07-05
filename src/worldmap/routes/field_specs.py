#!/usr/bin/env python3
"""Declarative widget specs for the schema-driven config UI (architecture review
candidate "htmx for the configuration UI").

Each FIELD_SPECS entry keys (section, option) to the widget that renders and
validates it, replacing the option-name string-matching dispatch in the legacy
client-side config JS (ui/config/index.html's ~46-branch renderTabContainers). A
field with no entry falls back to the existing generic text/number widget -- both
for genuinely generic options and, during the tab-by-tab migration, for any option
not yet ported from the legacy JS.

Only the "Global" tab (common + animation sections) is populated so far; further
tabs add entries here as they migrate.

Validated with ast.parse.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ToggleSpec:
    kind: str = field(default="toggle", init=False)


@dataclass(frozen=True)
class SliderSpec:
    min: float
    max: float
    step: float
    # Badge display: None = show the raw value verbatim (matches fields whose
    # legacy JS badge did no reformatting); an int fixes the decimal places shown.
    decimals: int | None = None
    suffix: str = ""
    kind: str = field(default="slider", init=False)


@dataclass(frozen=True)
class SelectSpec:
    options: list  # [(value, label), ...]
    kind: str = field(default="select", init=False)


FIELD_SPECS = {
    ("common", "basemap"): SelectSpec([
        ("satellite", "Satellite"),
        ("hybrid", "Satellite + Labels"),
        ("streets-v2", "Streets"),
        ("outdoor-v2", "Outdoor / Terrain"),
        ("topo-v2", "Topographic"),
        ("dataviz-dark", "Dataviz Dark"),
        ("winter", "Winter"),
        ("basic-v2", "Basic"),
    ]),
    ("common", "atmosphere"): ToggleSpec(),
    ("common", "target_geometry"): SelectSpec([
        ("2048x1024", "2048x1024"),
        ("4096x2048", "4096x2048"),
        ("8192x4096", "8192x4096"),
    ]),
    ("common", "auto_rotate"): ToggleSpec(),
    ("common", "auto_rotate_speed"): SliderSpec(min=0.01, max=1.0, step=0.01),
    # Fixes a pre-existing bug in the legacy JS, which swapped these two ranges
    # (latitude got +/-180, longitude got +/-90).
    ("common", "starting_latitude"): SliderSpec(
        min=-90.0, max=90.0, step=1.0, decimals=1, suffix=" deg"
    ),
    ("common", "starting_longitude"): SliderSpec(
        min=-180.0, max=180.0, step=1.0, decimals=1, suffix=" deg"
    ),
    ("common", "log_level"): SelectSpec([
        ("DEBUG", "DEBUG"),
        ("INFO", "INFO"),
        ("WARNING", "WARNING"),
        ("ERROR", "ERROR"),
        ("CRITICAL", "CRITICAL"),
    ]),
    ("animation", "forecast_stepping"): ToggleSpec(),
    ("animation", "stepping_rate"): SliderSpec(min=0, max=100, step=1),
}

# Per-(section, option) label overrides, ported from the legacy JS's customLabelText
# special cases -- only the ones relevant to fields with a FIELD_SPECS entry so far.
_LABEL_OVERRIDES = {
    ("animation", "forecast_stepping"): "Forecast stepping (hourly playback)",
    ("animation", "stepping_rate"): "Forecast stepping rate",
}


def field_label(section: str, option: str) -> str:
    override = _LABEL_OVERRIDES.get((section, option))
    if override is not None:
        return override
    spaced = option.replace("_", " ")
    return spaced[:1].upper() + spaced[1:]


def clamp_slider_value(spec: SliderSpec, value) -> float:
    """A stored value outside [min, max] (e.g. left over from a range that has since
    been corrected, or from an unvalidated write predating validate_against_specs)
    would otherwise make the rendered badge and the range input's clamped position
    disagree. Clamping once, before either is rendered, keeps them consistent."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return spec.min
    return max(spec.min, min(spec.max, v))


def format_slider_badge(spec: SliderSpec, value) -> str:
    if spec.decimals is None:
        return f"{value}{spec.suffix}"
    return f"{float(value):.{spec.decimals}f}{spec.suffix}"


def validate_against_specs(payload: dict) -> list[str]:
    """Check payload values with a FIELD_SPECS entry against that spec. Fields
    without an entry are left untouched -- same permissive behaviour as today."""
    errors = []
    for (section, option), spec in FIELD_SPECS.items():
        section_payload = payload.get(section)
        if not isinstance(section_payload, dict) or option not in section_payload:
            continue
        value = section_payload[option]

        if spec.kind == "slider":
            try:
                v = float(value)
            except (TypeError, ValueError):
                errors.append(f"{section}.{option}: expected a number, got {value!r}")
                continue
            if not (spec.min <= v <= spec.max):
                errors.append(
                    f"{section}.{option}: {v} outside [{spec.min}, {spec.max}]"
                )
        elif spec.kind == "select":
            valid = {opt_value for opt_value, _ in spec.options}
            if value not in valid:
                errors.append(
                    f"{section}.{option}: {value!r} not one of {sorted(valid)}"
                )
        elif spec.kind == "toggle":
            if not isinstance(value, bool):
                errors.append(f"{section}.{option}: expected true/false, got {value!r}")

    return errors
