#!/usr/bin/env python3
"""Declarative widget specs for the schema-driven config UI (architecture review
candidate "htmx for the configuration UI").

Each FIELD_SPECS entry keys (section, option) to the widget that renders and
validates it, replacing the option-name string-matching dispatch in the legacy
client-side config JS (ui/config/index.html's ~46-branch renderTabContainers). A
field with no entry falls back to the existing generic text/number widget -- both
for genuinely generic options and, during the tab-by-tab migration, for any option
not yet ported from the legacy JS. An unspecced boolean value still renders as a
toggle (not the number/text fallback) since every boolean in this config uses the
same widget regardless of field name -- matching the legacy JS's very first dispatch
check, `typeof value === "boolean"`, ahead of any option-name matching.

Many legacy branches matched on option name alone (e.g. any "*_hours" field, any
"*fontsize" field), independent of section -- a handful of module-level spec
constants below capture those shapes once and get registered under every
(section, option) pair that uses them, so the shape is defined a single time.

Migrated so far: Global (common, animation), Events (quakes, volcanoes),
Misc (satellites, terminator, markers), Shipping (shipping),
Atmospheric (clouds, isobars, wind, precipitation, pwat, lightning, storms),
Climate (sst, currents, waves, temperature, ozone, stormwatch).

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
    # value == 0 renders as this instead of the normal number+suffix (e.g. "off",
    # "keep forever") -- ported from legacy fields with a sentinel-value badge.
    zero_label: str | None = None
    # Appends "s" to `suffix` when the value isn't exactly 1 (e.g. "1 day" / "5 days").
    pluralize: bool = False
    # True only for clouds.threshold: the stored/posted value is a raw 0-255 byte,
    # but the slider displays/edits it as a 0-100 percentage. `min`/`max`/`step`
    # describe the DISPLAYED (percent) slider; `raw_max` is the stored value's actual
    # max, used by to_display_value (render) and validate_against_specs (POST).
    byte_to_percent: bool = False
    raw_max: float | None = None
    # CSS hook for the legacy saveActiveConfig() JS's per-class save dispatch (e.g.
    # "cloud-threshold-slider" triggers its percent -> byte reverse conversion).
    extra_class: str = ""
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
_ALPHA = SliderSpec(min=0, max=100, step=5)
_PARTICLE_SPEED_LIKE = SliderSpec(min=0, max=100, step=1)
_PARTICLE_SIZE = SliderSpec(min=0.1, max=5.0, step=0.05, decimals=2)
_TRAIL_FADE_OR_LENGTH = SliderSpec(min=0, max=100, step=1)
_MIN_MAX_C = SliderSpec(min=0, max=36, step=1, suffix=" DegC")
_CACHE_EXPIRY_DAYS = SliderSpec(
    min=0, max=30, step=1, suffix=" day", zero_label="keep forever", pluralize=True
)

_LEVEL_OF_DETAIL = SelectSpec([
    ("1", "Low resolution"),
    ("2", "Medium resolution"),
    ("3", "High resolution (needs lots of memory)"),
])

_MODE_OPTIONS = SelectSpec([
    ("absolute", "Absolute"),
    ("anomaly", "Anomaly"),
])

_LOG_LEVEL = SelectSpec([
    ("DEBUG", "DEBUG"),
    ("INFO", "INFO"),
    ("WARNING", "WARNING"),
    ("ERROR", "ERROR"),
    ("CRITICAL", "CRITICAL"),
])

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
    ("common", "log_level"): _LOG_LEVEL,
    ("animation", "forecast_stepping"): ToggleSpec(),
    ("animation", "stepping_rate"): _PARTICLE_SPEED_LIKE,
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
    ("terminator", "shade_opacity"): _ALPHA,
    ("terminator", "shade_color"): ColorSpec(named=False),
    ("terminator", "edge_softness"): SliderSpec(min=0, max=50, step=1),
    ("markers", "marker_color"): ColorSpec(),
    ("markers", "marker_fontsize"): _FONTSIZE,
    ("markers", "weather_popup"): ToggleSpec(),
    ("markers", "runs_per_day"): _RUNS_PER_DAY,
    # --- Shipping (shipping) ---
    ("shipping", "icon_zoom"): _ICON_ZOOM,
    ("shipping", "runs_per_day"): _RUNS_PER_DAY,
    # --- Atmospheric (clouds, isobars, wind, precipitation, pwat, lightning, storms) ---
    ("clouds", "threshold"): SliderSpec(
        min=0, max=100, step=1, suffix="%",
        byte_to_percent=True, raw_max=255, extra_class="cloud-threshold-slider",
    ),
    ("clouds", "gamma"): SliderSpec(min=0.1, max=3.0, step=0.05, decimals=2, prefix="γ "),
    ("clouds", "offset_days"): SliderSpec(min=0, max=7, step=1, suffix=" days"),
    ("clouds", "expiry_hours"): _HOURS,
    ("clouds", "runs_per_day"): _RUNS_PER_DAY,
    ("clouds", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("isobars", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("isobars", "isobar_color"): ColorSpec(),
    ("isobars", "linewidth"): SliderSpec(min=0.1, max=5.0, step=0.1, decimals=1, suffix="px"),
    ("isobars", "alpha"): _ALPHA,
    ("isobars", "label_fontsize"): _FONTSIZE,
    ("isobars", "label_outline"): ToggleSpec(),
    ("isobars", "runs_per_day"): _RUNS_PER_DAY,
    ("isobars", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("wind", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("wind", "render_mode"): SelectSpec([("trails", "Trails"), ("streaks", "Streaks")]),
    ("wind", "flow_coherence_radius"): SliderSpec(min=0.0, max=10.0, step=0.5, decimals=2),
    ("wind", "trail_persist"): SliderSpec(min=0.8, max=1.5, step=0.01, decimals=2),
    ("wind", "point_size"): SliderSpec(min=1, max=8, step=1, suffix="px"),
    ("wind", "vector_color"): ColorSpec(),
    ("wind", "particle_speed"): _PARTICLE_SPEED_LIKE,
    ("wind", "particle_alpha"): _ALPHA,
    ("wind", "particle_size"): _PARTICLE_SIZE,
    ("wind", "trail_fade"): _TRAIL_FADE_OR_LENGTH,
    ("wind", "heatmap_opacity"): _ALPHA,
    ("wind", "alpha"): _ALPHA,
    ("wind", "runs_per_day"): _RUNS_PER_DAY,
    ("wind", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("precipitation", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("precipitation", "min_mm_hr"): SliderSpec(min=0.0, max=10.0, step=0.1, decimals=1),
    ("precipitation", "alpha"): _ALPHA,
    ("precipitation", "palette"): SelectSpec([
        ("standard", "Standard"),
        ("ocean_blue", "Ocean blue"),
        ("high_contrast", "High contrast"),
    ]),
    ("precipitation", "key_fontsize"): _FONTSIZE,
    ("precipitation", "runs_per_day"): _RUNS_PER_DAY,
    ("precipitation", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("pwat", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("pwat", "palette"): SelectSpec([
        ("standard", "Standard (matches precipitation)"),
        ("atmospheric_river", "Atmospheric river (blue -> violet)"),
        ("deep_teal", "Deep teal (cyan -> teal)"),
    ]),
    ("pwat", "critical_pwat"): SliderSpec(min=0.0, max=80.0, step=5.0, decimals=0, suffix="mm"),
    ("pwat", "alpha"): _ALPHA,
    ("pwat", "key_fontsize"): _FONTSIZE,
    ("pwat", "runs_per_day"): _RUNS_PER_DAY,
    ("pwat", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("lightning", "icon_zoom"): _ICON_ZOOM,
    ("lightning", "strike_recent_minutes"): _MINUTES,
    ("lightning", "strike_keep_minutes"): _MINUTES,
    ("lightning", "strike_expiry_hours"): _HOURS,
    ("lightning", "runs_per_day"): _RUNS_PER_DAY,
    ("storms", "icon_zoom"): _ICON_ZOOM,
    ("storms", "storm_name_fontsize"): _FONTSIZE,
    ("storms", "forecast_cone_alpha"): _ALPHA,
    ("storms", "forecast_cone_color"): ColorSpec(),
    ("storms", "storm_track_color"): ColorSpec(),
    ("storms", "expiry_days"): SliderSpec(min=0, max=60, step=1, suffix=" days expiry"),
    ("storms", "runs_per_day"): _RUNS_PER_DAY,
    # --- Climate (sst, currents, waves, temperature, ozone, stormwatch) ---
    ("sst", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("sst", "mode"): _MODE_OPTIONS,
    ("sst", "alpha"): _ALPHA,
    ("sst", "palette"): SelectSpec([
        ("thermal", "Thermal"),
        ("vivid", "Vivid"),
        ("deep", "Deep"),
        ("ocean", "Ocean"),
    ]),
    ("sst", "min_c"): _MIN_MAX_C,
    ("sst", "max_c"): _MIN_MAX_C,
    ("sst", "key_fontsize"): _FONTSIZE,
    ("sst", "runs_per_day"): _RUNS_PER_DAY,
    ("sst", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("currents", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("currents", "palette"): SelectSpec([
        ("thermal_red", "Thermal red"),
        ("electric_blue", "Electric blue"),
        ("toxic_neon", "Toxic neon"),
        ("cyberpunk", "Cyberpunk"),
    ]),
    ("currents", "alpha"): _ALPHA,
    ("currents", "particle_speed"): _PARTICLE_SPEED_LIKE,
    ("currents", "current_speed_minimum"): SliderSpec(
        min=0.0, max=5.0, step=0.1, decimals=2, suffix=" m/s"
    ),
    ("currents", "trail_length"): _TRAIL_FADE_OR_LENGTH,
    ("currents", "key_fontsize"): _FONTSIZE,
    ("currents", "runs_per_day"): _RUNS_PER_DAY,
    ("currents", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("waves", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("waves", "palette"): SelectSpec([
        ("ocean_storm", "Ocean storm"),
        ("neon_surge", "Neon surge"),
        ("solar_flare", "Solar flare"),
    ]),
    ("waves", "alpha"): _ALPHA,
    ("waves", "min_wave_height"): SliderSpec(min=0, max=5, step=0.25, suffix=" m", zero_label="off"),
    ("waves", "key_fontsize"): _FONTSIZE,
    ("waves", "runs_per_day"): _RUNS_PER_DAY,
    ("waves", "particle_speed"): _PARTICLE_SPEED_LIKE,
    ("waves", "particle_size"): _PARTICLE_SIZE,
    ("waves", "bar_length"): SliderSpec(min=1, max=8, step=1),
    ("waves", "particle_alpha"): _ALPHA,
    ("waves", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("temperature", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("temperature", "mode"): _MODE_OPTIONS,
    ("temperature", "palette"): SelectSpec([
        ("global_thermal", "Global thermal"),
        ("extreme_contrast", "Extreme contrast"),
        ("twilight_gradient", "Twilight gradient"),
    ]),
    ("temperature", "alpha"): _ALPHA,
    ("temperature", "show_freezing_line"): ToggleSpec(),
    ("temperature", "key_fontsize"): _FONTSIZE,
    ("temperature", "runs_per_day"): _RUNS_PER_DAY,
    ("temperature", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("ozone", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("ozone", "palette"): SelectSpec([
        ("alert", "Alert (magenta -> yellow)"),
        ("high_contrast", "High contrast (red -> pale yellow)"),
    ]),
    ("ozone", "critical_du"): SliderSpec(min=150.0, max=500.0, step=10.0, decimals=1, suffix="du"),
    ("ozone", "alpha"): _ALPHA,
    ("ozone", "key_fontsize"): _FONTSIZE,
    ("ozone", "runs_per_day"): _RUNS_PER_DAY,
    ("ozone", "stormwatch"): ToggleSpec(),
    ("ozone", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    ("stormwatch", "level_of_detail"): _LEVEL_OF_DETAIL,
    ("stormwatch", "min_cape"): SliderSpec(min=0, max=5000, step=100, suffix="J/Kg"),
    ("stormwatch", "alpha"): _ALPHA,
    ("stormwatch", "key_fontsize"): _FONTSIZE,
    ("stormwatch", "runs_per_day"): _RUNS_PER_DAY,
    ("stormwatch", "cache_expiry_days"): _CACHE_EXPIRY_DAYS,
    # --- Background (shipping_collector, lightning_collector, satellites_collector,
    # data_collector, housekeeper) ---
    ("shipping_collector", "log_level"): _LOG_LEVEL,
    ("lightning_collector", "expiry_hours"): _HOURS,
    ("lightning_collector", "log_level"): _LOG_LEVEL,
    ("satellites_collector", "update_hours"): _HOURS,
    ("satellites_collector", "log_level"): _LOG_LEVEL,
    # data_collector.datasources is deliberately NOT here -- see
    # render_datasources_accordion in _field_macros.html.
    ("data_collector", "update_minutes"): _MINUTES,
    ("data_collector", "cache_hours"): _HOURS,
    ("data_collector", "log_level"): _LOG_LEVEL,
    ("housekeeper", "enabled"): ToggleSpec(),
    ("housekeeper", "days_between_runs"): SliderSpec(
        min=1, max=14, step=1, prefix="every ", suffix=" day", pluralize=True
    ),
    ("housekeeper", "field_expiry_hours"): _HOURS,
    ("housekeeper", "dry_run"): ToggleSpec(),
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
    ("stormwatch", "min_cape"): "Minimum CAPE Threshold",
    ("ozone", "critical_du"): "Critical Ozone Threshold (Dobson Units)",
    ("pwat", "critical_pwat"): "Critical Moisture Threshold (mm)",
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


# Friendly section headings for the "X Properties" title above each settings block --
# the same name used for that layer's toggle/radio in the Show tab, so both parts of
# the UI call a layer by the same name instead of the raw config key in brackets
# (e.g. "Precipitable Water Properties", not "[pwat] Properties").
SECTION_LABELS = {
    "clouds": "Clouds",
    "isobars": "Isobars",
    "wind": "Wind",
    "precipitation": "Precipitation",
    "pwat": "Precipitable Water",
    "lightning": "Lightning",
    "storms": "Storm Track",
    "sst": "Sea Surface Temp",
    "currents": "Ocean Currents",
    "waves": "Wave Heights",
    "temperature": "Air Temperature",
    "ozone": "Ozone",
    "stormwatch": "Storm Watch",
    "quakes": "Earthquakes",
    "volcanoes": "Volcanoes",
    "satellites": "Satellites",
    "terminator": "Terminator Night/day Shade",
    "markers": "Place Markers",
    "shipping": "Shipping Overlay",
    "shipping_collector": "Shipping Collector (AIS Loop)",
    "lightning_collector": "Lightning Collector Daemon",
    "satellites_collector": "Satellites Collector",
    "data_collector": "Data Collector",
}


def section_label(section: str) -> str:
    """Friendly "X Properties" heading for a settings section -- matches the Show
    tab's label for that layer's toggle/radio exactly. Sections with no Show-tab entry
    (map_builder, animation, housekeeper) fall back to a title-cased, space-split
    version of the section key."""
    return SECTION_LABELS.get(section, section.replace("_", " ").title())


def to_display_value(spec: SliderSpec, raw_value):
    """Converts a stored value into the space the HTML slider actually operates in.
    Only clouds.threshold uses this today (raw 0-255 byte, displayed/edited as a
    0-100 percentage) -- everything else is a no-op."""
    if not spec.byte_to_percent:
        return raw_value
    try:
        raw = float(raw_value)
    except (TypeError, ValueError):
        raw = 0
    return round((raw / spec.raw_max) * spec.max)


def clamp_slider_value(spec: SliderSpec, value) -> float:
    """A stored value outside [min, max] (e.g. left over from a range that has since
    been corrected, or from an unvalidated write predating validate_against_specs)
    would otherwise make the rendered badge and the range input's clamped position
    disagree. Clamping once, before either is rendered, keeps them consistent.

    Whole-step sliders (min_cape, runs_per_day, fontsize, ...) match legacy JS
    branches that used parseInt for the badge -- always a clean int ("45"), never
    "45.0". Returning an int here (rather than float(value)'s float) means
    format_slider_badge doesn't need to re-derive that on every call."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = spec.min
    else:
        v = max(spec.min, min(spec.max, v))
    if float(spec.step).is_integer():
        return int(round(v))
    return v


def format_slider_badge(spec: SliderSpec, value) -> str:
    if spec.zero_label is not None:
        try:
            if float(value) == 0:
                return spec.zero_label
        except (TypeError, ValueError):
            pass

    base = str(value) if spec.decimals is None else f"{float(value):.{spec.decimals}f}"
    suffix = spec.suffix
    if spec.pluralize:
        try:
            count = float(value)
        except (TypeError, ValueError):
            count = None
        if count is not None and count != 1:
            suffix = f"{suffix}s"
    return f"{spec.prefix}{base}{suffix}"


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
            # byte_to_percent fields are posted in raw/stored space (0-255), not the
            # displayed slider's 0-100 percent space.
            hi = spec.raw_max if spec.raw_max is not None else spec.max
            if not (spec.min <= v <= hi):
                errors.append(f"{section}.{option}: {v} outside [{spec.min}, {hi}]")
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
