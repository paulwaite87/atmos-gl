#!/usr/bin/env python3
"""Declarative widget specs for the schema-driven config UI (architecture review
candidate "htmx for the configuration UI").

Each FIELD_SPECS entry keys (section, option) to the widget that renders and
validates it, replacing the option-name string-matching dispatch in the legacy
client-side config JS (ui/config/index.html's ~46-branch renderTabContainers). A
field with no entry falls back to the existing generic text/number widget -- both
for genuinely generic options and, during the tab-by-tab migration, for any option
not yet ported from the legacy JS.

Many legacy branches matched on option name alone (e.g. any "*_hours" field, any
"*fontsize" field), independent of section -- a handful of module-level spec
constants below capture those shapes once and get registered under every
(section, option) pair that uses them, so the shape is defined a single time.

Migrated so far: Global (common, animation), Events (quakes, volcanoes),
Misc (satellites, terminator, markers), Shipping (shipping).

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
    prefix: str = ""
    suffix: str = ""
    kind: str = field(default="slider", init=False)


@dataclass(frozen=True)
class SelectSpec:
    options: list  # [(value, label), ...]
    kind: str = field(default="select", init=False)


@dataclass(frozen=True)
class MultiSelectSpec:
    options: list  # [(value, label), ...]
    kind: str = field(default="multiselect", init=False)


@dataclass(frozen=True)
class ColorSpec:
    # True (the common case): saved as the nearest named colour (e.g. "White"),
    # like markers.marker_color / volcanoes.marker_color. False: saved as the raw
    # hex string, like terminator.shade_color -- ported from the legacy JS's
    # pickerClass distinction (option.includes('_default_') or section == 'terminator'
    # got the raw-hex behaviour; everything else got the named-colour behaviour).
    named: bool = True
    kind: str = field(default="color", init=False)


# Mirrors the client-side COLOR_MAP in templates/config.html -- needed server-side
# only to resolve a stored *name* (e.g. "white") to its initial hex swatch value;
# the reverse direction (hex -> nearest name) stays client-side in findNearestNamedColor,
# used live as the user drags the picker.
COLOR_MAP = {
    "white": "#ffffff", "black": "#000000", "gray": "#808080", "silver": "#c0c0c0",
    "red": "#ff0000", "maroon": "#800000", "pink": "#ffc0cb",
    "green": "#00ff00", "lime": "#00ff00", "olive": "#808000", "teal": "#008080",
    "blue": "#0000ff", "navy": "#000080", "cyan": "#00ffff", "aqua": "#00ffff",
    "yellow": "#ffff00", "orange": "#ffa500", "gold": "#ffd700",
    "purple": "#800080", "magenta": "#ff00ff", "violet": "#ee82ee",
}


def initial_color_render(value) -> tuple[str, str]:
    """(hex, label) for a color field's initial render -- ported verbatim from the
    legacy JS (which capitalizes the raw stored string rather than computing the
    nearest named color; that computation only happens client-side, on interaction)."""
    raw = str(value).lower().strip()
    hex_value = raw if raw.startswith("#") else COLOR_MAP.get(raw, "#ffffff")
    label = (raw[:1].upper() + raw[1:]) if raw else "White"
    return hex_value, label


# --- Shared shapes: legacy branches matched these purely on option name,
# regardless of section, so one instance is registered under every field that
# uses it (see FIELD_SPECS below). ---

_ICON_ZOOM = SliderSpec(min=0.1, max=5.0, step=0.1, decimals=1, suffix="x")
_HOURS = SliderSpec(min=0, max=96, step=1, suffix="h")
_MINUTES = SliderSpec(min=0, max=120, step=1, suffix="mins")
_FONTSIZE = SliderSpec(min=6, max=24, step=1, suffix="px")
_RUNS_PER_DAY = SliderSpec(min=1, max=24, step=1, suffix=" runs")

_VEI_OPTIONS = SelectSpec([
    ("0", "0 - Non-explosive"),
    ("1", "1 - Small"),
    ("2", "2 - Moderate"),
    ("3", "3 - Moderate-Large"),
    ("4", "4 - Large"),
    ("5", "5 - Very Large"),
    ("6", "6 - Paroxysmal"),
    ("7", "7 - Colossal"),
    ("8", "8 - Super colossal"),
])

_ERUPT_DATE_CODES = MultiSelectSpec([
    ("D1", "D1 - 1964 or later"),
    ("D2", "D2 - 1900 to 1963"),
    ("D3", "D3 - 1800 to 1899"),
    ("D4", "D4 - 1700 to 1799"),
    ("D5", "D5 - 1500 to 1699"),
    ("D6", "D6 - A.D.1 to 1499"),
    ("D7", "D7 - B.C. (Holocene)"),
    ("U", "U  - Undated, prob. Holocene"),
    ("Q", "Q  - Quaternary (very old!)"),
    ("?", "?  - Uncertain Holocene"),
])

_SAT_NAMES = MultiSelectSpec([
    ("ISS (ZARYA)", "ISS (ZARYA) - International Space Station"),
    ("CSS (TIANHE)", "CSS (TIANHE)  - Chinese Space Station"),
    ("HST", "HST - Hubble Space Telescope"),
    ("FGRST (GLAST)", "FGRST (GLAST) - The Fermi Gamma-ray Space Telescope"),
    ("SWIFT", "SWIFT - The Neil Gehrels Swift Observatory"),
    ("NOAA 15", "NOAA 15 - The Polar orbiting weather fleet"),
    ("NOAA 18", "NOAA 18"),
    ("NOAA 19", "NOAA 19"),
    ("NOAA 20", "NOAA 20"),
    ("NOAA 21", "NOAA 21"),
    ("AQUA", "AQUA - NASA flagship water-cycle observer."),
    ("TERRA", "TERRA - Twin to Aqua, tasked with mapping land mass and vegetation"),
    ("LANDSAT 8", "LANDSAT 8 - Legendary optical and thermal Earth-imaging satellite"),
    ("LANDSAT 9", "LANDSAT 9 - The newest Landsat satellite"),
    ("SENTINEL-1A", "SENTINEL-1A - European Space Agency primary radar imaging satellite"),
    ("GOES 16", "GOES 16 - Geostationary (Americas/Atlantic)"),
    ("GOES 18", "GOES 18 - Geostationary (Pacific/Americas)"),
    ("METEOSAT-9", "METEOSAT-9 - Geostationary (Indian Ocean)"),
    ("METEOSAT-10", "METEOSAT-10 - Geostationary (Europe/Africa)"),
    ("METEOSAT-11", "METEOSAT-11 - Geostationary (Prime Meridian)"),
])


FIELD_SPECS = {
    # --- Global (common, animation) ---
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
    # --- Events (quakes, volcanoes) ---
    ("quakes", "icon_zoom"): _ICON_ZOOM,
    ("quakes", "recent_activity_hours"): _HOURS,
    ("quakes", "expiry_hours"): _HOURS,
    ("quakes", "label_fontsize"): _FONTSIZE,
    ("quakes", "min_mag"): SliderSpec(min=0, max=10, step=0.1, decimals=1, prefix="M "),
    ("quakes", "runs_per_day"): _RUNS_PER_DAY,
    ("volcanoes", "marker_color"): ColorSpec(),
    ("volcanoes", "significant_only"): ToggleSpec(),
    ("volcanoes", "vei_min"): _VEI_OPTIONS,
    ("volcanoes", "erupt_date_codes"): _ERUPT_DATE_CODES,
    ("volcanoes", "runs_per_day"): _RUNS_PER_DAY,
    # --- Misc (satellites, terminator, markers) ---
    ("satellites", "sat_names"): _SAT_NAMES,
    ("satellites", "past_minutes"): _MINUTES,
    ("satellites", "future_minutes"): _MINUTES,
    ("terminator", "shade_opacity"): SliderSpec(min=0, max=100, step=5),
    ("terminator", "shade_color"): ColorSpec(named=False),
    ("terminator", "edge_softness"): SliderSpec(min=0, max=50, step=1),
    ("markers", "marker_color"): ColorSpec(),
    ("markers", "marker_fontsize"): _FONTSIZE,
    ("markers", "weather_popup"): ToggleSpec(),
    ("markers", "runs_per_day"): _RUNS_PER_DAY,
    # --- Shipping (shipping) ---
    ("shipping", "icon_zoom"): _ICON_ZOOM,
    ("shipping", "runs_per_day"): _RUNS_PER_DAY,
}

# Option-name-only label overrides, checked BEFORE the (section, option) overrides
# below -- ported from the legacy JS's customLabelText, which checked option === "outfile"
# unconditionally, ahead of any section-specific case.
_GENERIC_LABEL_OVERRIDES = {
    "outfile": "Output file",
}

# Per-(section, option) label overrides, ported from the legacy JS's customLabelText
# special cases -- only the ones relevant to fields with a FIELD_SPECS entry so far.
_LABEL_OVERRIDES = {
    ("animation", "forecast_stepping"): "Forecast stepping (hourly playback)",
    ("animation", "stepping_rate"): "Forecast stepping rate",
    ("quakes", "min_mag"): "Minimum magnitude",
}


def field_label(section: str, option: str) -> str:
    generic_override = _GENERIC_LABEL_OVERRIDES.get(option)
    if generic_override is not None:
        return generic_override
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
        return f"{spec.prefix}{value}{spec.suffix}"
    return f"{spec.prefix}{float(value):.{spec.decimals}f}{spec.suffix}"


def is_long_or_url_field(option: str, value) -> bool:
    """Ported from the legacy JS's fallback branch: a long value or a *url*-named
    option renders full-width instead of the default half-width column."""
    return len(str(value)) > 35 or "url" in option


def is_api_key_field(option: str) -> bool:
    """Secrets injected by WorldMapConfig._inject_secrets (e.g. common.api_key,
    shipping_collector.api_key) render read-only, matching the legacy JS."""
    return "api_key" in option


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
            # Some stored values are ints (e.g. volcanoes.vei_min) even though option
            # values are declared as strings -- compare as strings, like the rendering
            # macro's "selected" check, so a legitimate value isn't rejected.
            valid = {str(opt_value) for opt_value, _ in spec.options}
            if str(value) not in valid:
                errors.append(
                    f"{section}.{option}: {value!r} not one of {sorted(valid)}"
                )
        elif spec.kind == "multiselect":
            valid = {str(opt_value) for opt_value, _ in spec.options}
            if not isinstance(value, list) or not all(str(v) in valid for v in value):
                errors.append(
                    f"{section}.{option}: {value!r} not a subset of {sorted(valid)}"
                )
        elif spec.kind == "toggle":
            if not isinstance(value, bool):
                errors.append(f"{section}.{option}: expected true/false, got {value!r}")
        # ColorSpec: matches the legacy JS's permissiveness -- any string is accepted
        # (colors are freeform hex/name text, not a closed option set).

    return errors
