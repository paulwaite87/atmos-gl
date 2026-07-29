#!/usr/bin/env python3
"""AirQualityUpdater: renders the CAMS PM2.5/PM10/smoke-AOD forecast cache
(collectors/air_quality.py) to the air_quality layer's PNGs.

Absolute (current-conditions) only -- there is no baseline/anomaly pair for this
layer, so this mirrors GhgUpdater (tasks/greenhouse_gases.py) minus the anomaly-mode
branch entirely: one variable axis instead of a (species, mode) pair, and a fixed
AQI-style colour gradient instead of a user-selectable palette.
"""
import gc
import logging
import os

import cartopy.crs as ccrs
import matplotlib.colors as mcolors
import numpy as np

from atmos_gl.lib.air_quality import VARIABLES, camsforecast_cache_path
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.netcdf_field import load_field
from .common import Updater, MapData
from .plotting import Plot, clamp_lats_to_mercator_limit

logger = logging.getLogger(__name__)

# Same rendering-cost reasoning as greenhouse_gases.py's _REGRID_STEP_DEG: CAMS's
# native ~0.4 deg forecast grid is already coarser than GHG's, but 3 variables
# rendered every cycle still benefits from capping at the same LOD tier this app's
# other raster layers use.
_REGRID_STEP_DEG = 0.25

# In-file netCDF variable names -- confirmed by downloading and inspecting a real CAMS
# atmospheric-composition-forecasts file (see the published spec's issue comments).
# NOT the same as the request-time CDS variable identifiers (particulate_matter_2.5um/
# particulate_matter_10um/total_aerosol_optical_depth_550nm, used in
# collectors/air_quality.py's request builder) -- ECMWF's GRIB-to-netCDF conversion
# exposes them under their short GRIB_cfVarName instead.
_CAMS_VARS = {"pm2_5": "pm2p5", "pm10": "pm10", "aod": "aod550"}

# PM2.5/PM10 are delivered in kg/m^3 (confirmed from the file's own `units`
# attribute); the display convention (and the spec's default scale ranges) is
# µg/m^3, matching how every phone weather app and AQI monitor reports particulates.
# AOD is dimensionless -- no conversion.
_UNIT_SCALE = {"pm2_5": 1e9, "pm10": 1e9, "aod": 1.0}
_DISPLAY_UNIT = {"pm2_5": "µg/m³", "pm10": "µg/m³", "aod": ""}
_DISPLAY_LABEL = {"pm2_5": "PM2.5", "pm10": "PM10", "aod": "Smoke (AOD)"}
_TICK_FORMAT = {"pm2_5": "%d", "pm10": "%d", "aod": "%.2f"}

# Flat, variable-prefixed setting key (pm2_5_min, aod_min, ...) -- same convention
# greenhouse_gases.py's _SCALE_SETTING_KEYS uses, since FIELD_SPECS/validate_against_specs
# (routes/field_specs.py) only understands flat (section, option) keys. Only a MINIMUM
# is user-configurable, not a max -- see _FIXED_CEILING below.
_MIN_SETTING_KEY = {"pm2_5": "pm2_5_min", "pm10": "pm10_min", "aod": "aod_min"}
_DEFAULT_MIN = {"pm2_5": 0, "pm10": 0, "aod": 0}

# Fixed, non-configurable top of the colour gradient -- confirmed live against real
# CAMS data (see the published spec's issue comments): AOD rarely exceeds ~2.5 even
# during extreme Saharan dust events, PM2.5/PM10 defaults chosen to match. Originally
# a second user-adjustable slider (aod_max/pm2_5_max/pm10_max), but live testing found
# an independent min+max pair invites a scale (e.g. min=1, max=5) that clips nearly
# the entire globe to the gradient's bottom colour -- indistinguishable from "the
# layer is broken" even though the render was correct. A single min threshold against
# a realistic fixed ceiling is a much harder scale to misconfigure.
_FIXED_CEILING = {"pm2_5": 250, "pm10": 400, "aod": 3}

# Fixed AQI-recognisable gradient (green -> yellow -> orange -> red -> purple),
# matching the colour convention of every mainstream phone weather app's air-quality
# widget -- NOT one of this app's other raster layers' user-selectable named
# palettes, and not exposed through that palette-picker UI control (see the published
# spec's Implementation Decisions).
_AQI_COLORS = ["#00e400", "#ffff00", "#ff7e00", "#ff0000", "#8f3f97"]
_AQI_CMAP = mcolors.LinearSegmentedColormap.from_list("aqi", _AQI_COLORS)


class AirQualityUpdater(Updater):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "air_quality", map_data)
        self.variable = self.settings.get("variable", "pm2_5").strip().lower()

    def _output_path_for(self, variable: str) -> str:
        """Per-variable, ALWAYS-kept-fresh output path: 'data/air_quality.png' ->
        e.g. 'data/air_quality_pm2_5.png'. All 3 variables render here every cycle
        (independent of the configured variable) so the frontend can switch between
        them instantly -- see ui/modules/air_quality.js."""
        base, ext = os.path.splitext(self.output_path)
        return f"{base}_{variable}{ext}"

    def plot(self, variable: str, current_nc: str, output_path: str):
        alpha = float(self.settings.get("opacity", 60) / 100)

        display_data, lat_raw, lon_norm = load_field(current_nc, _CAMS_VARS[variable])
        display_data = display_data * _UNIT_SCALE[variable]

        new_lats, new_lons, display_data = self.regrid_for_lod(
            display_data, lat_raw, lon_norm, fill_value=np.nan, step_override=_REGRID_STEP_DEG,
        )

        min_key = _MIN_SETTING_KEY[variable]
        vmin = self.settings.get(min_key, _DEFAULT_MIN[variable])
        vmax = _FIXED_CEILING[variable]

        # No land mask -- air quality genuinely applies over land too (a user-reported
        # regression: this used to gray out every landmass with a SST/GHG-style tint,
        # which made no physical sense for an atmospheric variable). Instead, values
        # BELOW the configured minimum become NaN, which pcolormesh simply doesn't
        # draw (transparent, showing the base map through) -- so only areas actually
        # above the threshold are highlighted, rather than clipping everything below
        # it to the gradient's solid bottom colour (indistinguishable from "broken").
        display_data = np.where(display_data < vmin, np.nan, display_data)

        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
        unit = _DISPLAY_UNIT[variable]
        title_text = f"{_DISPLAY_LABEL[variable]} ({unit})" if unit else _DISPLAY_LABEL[variable]

        plot = Plot(self.map_data.region)
        plot.get_figure()
        plot.ax.pcolormesh(
            new_lons,
            clamp_lats_to_mercator_limit(new_lats),
            display_data,
            transform=ccrs.PlateCarree(),
            cmap=_AQI_CMAP,
            norm=norm,
            alpha=alpha,
            shading="nearest",
            rasterized=True,
            zorder=2,
        )
        plot.save_figure(output_path)
        calculated_ticks = np.linspace(vmin, vmax, 5)
        self.save_key_image(
            output_path,
            _AQI_CMAP,
            norm,
            calculated_ticks,
            title_text,
            key_fontsize=self.common.get("key_fontsize", 10),
            labelsize=8,
            tick_format=_TICK_FORMAT[variable],
            weight="bold",
        )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()
        gc.collect()

        logger.debug(f"Successfully rendered {variable} air quality map.")

    def _variable_settings_signature(self, variable: str) -> str:
        """Render-relevant settings for `variable`, for _is_render_fresh -- opacity and
        key_fontsize are baked into the rendered pixels (see plot()'s alpha and
        save_key_image's key_fontsize); min selects both the fixed colour gradient's
        bottom AND the below-threshold transparency cutoff (no palette or max setting
        exists for this layer -- see _FIXED_CEILING)."""
        min_key = _MIN_SETTING_KEY[variable]
        values = {
            "opacity": self.settings.get("opacity", 60),
            "key_fontsize": self.common.get("key_fontsize", 10),
            "min": self.settings.get(min_key, _DEFAULT_MIN[variable]),
        }
        return self._settings_signature(values)

    def run(self, max_hours=None):
        # max_hours is a no-op here -- air quality renders once per cycle per variable,
        # not per forecast hour. Accepted only so layer_builder's dispatch can call
        # every TASK_CLASSES entry's run() the same way.
        current_nc = camsforecast_cache_path(self.workdir)
        if not os.path.exists(current_nc):
            logger.info(
                "Air quality: CAMS forecast cache not present yet "
                "(data collector hasn't fetched it); skipping."
            )
            return

        for variable in VARIABLES:
            out = self._output_path_for(variable)
            sig = self._variable_settings_signature(variable)
            fresh = self._is_render_fresh(out, [current_nc], sig)
            if not fresh:
                logger.info(f"Generating air quality {variable} plot...")
                self.plot(variable, current_nc, out)
                self._write_render_signature(out, sig)

        # Publish whichever variable is currently configured -- unconditionally of
        # whether it needed re-rendering above, same as GhgUpdater.run().
        self._publish_variant(self._output_path_for(self.variable))
