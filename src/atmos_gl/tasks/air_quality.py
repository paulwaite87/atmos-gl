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

import matplotlib.colors as mcolors
import numpy as np
from scipy.ndimage import gaussian_filter
from scipy.interpolate import RegularGridInterpolator

from atmos_gl.lib.air_quality import VARIABLES, camsforecast_cache_path
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.netcdf_field import load_field
from .common import Updater, MapData
from .plotting import Plot, MERCATOR_LAT_LIMIT

# Earth radius (metres) used by cartopy's WEB_MERCATOR (ccrs.Mercator.GOOGLE,
# EPSG:3857) -- confirmed live to match it exactly (ccrs.Mercator.GOOGLE's own proj
# string is "+a=6378137.0 +b=6378137.0", a sphere, not an ellipsoid). Used to pre-warp
# this layer's data to Mercator's own Y spacing in closed form -- see plot()'s comment
# for why.
_MERCATOR_R = 6378137.0

logger = logging.getLogger(__name__)

# Same rendering-cost reasoning as greenhouse_gases.py's _REGRID_STEP_DEG: CAMS's
# native ~0.4 deg forecast grid is already coarser than GHG's, but 3 variables
# rendered every cycle still benefits from capping at the same LOD tier this app's
# other raster layers use.
_REGRID_STEP_DEG = 0.25

# Gaussian-blur sigma (in regrid cells) applied to the field before plotting --
# CAMS's native ~0.4 deg grid is coarser than the 0.25 deg regrid target, so a raw
# pcolormesh render shows visibly blocky grid-cell edges (user-reported). Matches
# PrecipitationUpdater's "medium" LOD tier sigma (tasks/precipitation.py) -- this
# layer has no level_of_detail setting of its own, so one fixed value is used
# throughout rather than adding a tier concept purely for this.
_SMOOTH_SIGMA = 1.2

# Width (as a fraction of the visible vmin..vmax range) of the soft alpha ramp above
# the threshold -- see plot()'s RGBA construction. User-reported: even after the
# Gaussian blur above, a HARD on/off transparency cutoff at the threshold still shows
# a visible staircase at the 0.25 deg grid resolution once zoomed in in the browser,
# because every "on" vs "off" cell renders as a flat, fully-opaque-or-fully-transparent
# square with nothing in between for either matplotlib or the browser to interpolate
# across. Fading smoothly over this band removes the hard edge entirely.
_ALPHA_FEATHER_FRACTION = 0.15

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

# Config fallback when the setting is unset -- a UX/policy default (what a fresh
# install shows), NOT the same thing as the variable's natural floor (see
# _NATURAL_FLOOR below, used by plot()'s fade-gating). AOD's default of 0.5 is where
# NOAA/NASA smoke-monitoring research commonly places "smoke starting to matter for
# health" (surface PM2.5 crossing into Unhealthy-for-Sensitive-Groups territory during
# wildfire events) -- not an official regulatory breakpoint (AOD, a column-integrated
# quantity, doesn't have one the way surface PM2.5 does), but a reasonable single
# number if forced to pick one. PM2.5/PM10 stay at 0 (show everything) -- no equally
# well-established "start filtering here" number was found for those in this session.
_DEFAULT_MIN = {"pm2_5": 0, "pm10": 0, "aod": 0.5}

# The true physical minimum for each variable -- concentrations/AOD can't go negative,
# so this is always 0 for all three, REGARDLESS of what _DEFAULT_MIN ships as. Used
# only to decide whether plot()'s alpha fade should apply at all: at vmin == the
# natural floor there is no real threshold to feather (nothing in the data can be
# below it), so fading unconditionally would carve holes out of genuinely-present-but
# -low readings instead (see plot()'s comment). Comparing against _DEFAULT_MIN instead
# of this would break the moment _DEFAULT_MIN stops being 0 for some variable (as AOD
# no longer is) -- the two are deliberately kept as separate constants so that never
# happens silently again.
_NATURAL_FLOOR = {"pm2_5": 0, "pm10": 0, "aod": 0}

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

        # gaussian_filter doesn't handle NaN (it spreads/poisons neighbouring cells),
        # so fill the regrid's small overshoot-edge NaN sliver with 0 before blurring.
        display_data = gaussian_filter(np.nan_to_num(display_data, nan=0.0), sigma=_SMOOTH_SIGMA)

        min_key = _MIN_SETTING_KEY[variable]
        default_min = _DEFAULT_MIN[variable]
        vmin = self.settings.get(min_key, default_min)
        vmax = _FIXED_CEILING[variable]
        norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

        # No land mask -- air quality genuinely applies over land too (a user-reported
        # regression: this used to gray out every landmass with a SST/GHG-style tint,
        # which made no physical sense for an atmospheric variable).
        #
        # Below-threshold areas are transparent, but via a smooth per-pixel ALPHA FADE
        # rather than a hard NaN cutoff -- a binary on/off mask still showed a visible
        # staircase at the grid resolution once zoomed in (user-reported; see
        # _ALPHA_FEATHER_FRACTION above), since the transparency boundary itself was a
        # hard step with nothing to interpolate across. Built as an explicit
        # (Ny, Nx, 4) RGBA array.
        #
        # The fade is only applied when vmin is ABOVE the variable's natural floor
        # (_NATURAL_FLOOR, always 0 -- concentrations/AOD can't go negative). At
        # vmin == 0 the user has asked for "no threshold, show me everything", and
        # fading out genuinely-present-but-low readings (e.g. ~0.3 ug/m3 over clean
        # remote regions, real CAMS output, not noise) would carve visible transparent
        # holes out of what's supposed to be full coverage -- confirmed live.
        # Deliberately compared against _NATURAL_FLOOR, not _DEFAULT_MIN (which is a
        # different thing -- a UX default, currently 0.5 for AOD) -- a threshold the
        # user genuinely wants (the shipped AOD default included) must still get its
        # feathered edge; only the true, un-thresholdable floor needs none.
        rgba = _AQI_CMAP(norm(display_data))
        if vmin > _NATURAL_FLOOR[variable]:
            feather = max(vmax - vmin, 1e-9) * _ALPHA_FEATHER_FRACTION
            fade = np.clip((display_data - vmin) / feather, 0.0, 1.0)
        else:
            fade = np.ones_like(display_data)
        rgba[..., 3] = fade * alpha

        unit = _DISPLAY_UNIT[variable]
        title_text = f"{_DISPLAY_LABEL[variable]} ({unit})" if unit else _DISPLAY_LABEL[variable]

        # Rendered via a hand-rolled Web Mercator pre-warp + a plain, NON-cartopy
        # imshow() rather than pcolormesh/imshow's usual transform=ccrs.PlateCarree()
        # path -- three approaches were tried live and rejected before this one:
        #   1. pcolormesh(shading="nearest") -- blocky, un-interpolated grid cells,
        #      the original user-reported pixelation.
        #   2. pcolormesh(shading="gouraud") -- smoothly interpolates colour AND alpha,
        #      fixing (1)'s blockiness, but produces a visible fine dither/grain
        #      texture (an Agg-backend gouraud-rendering artifact, confirmed absent
        #      from "nearest" renders) that's still clearly visible at a normal,
        #      commonly-used browser zoom level, not just extreme close-up (user
        #      confirmed live).
        #   3. imshow(transform=ccrs.PlateCarree(), interpolation="bilinear") -- zero
        #      dithering, but only filled the centre HALF of the canvas -- confirmed
        #      via pixel bounding-box inspection, reproducible even with an explicit
        #      regrid_shape and completely independent of alpha/real data (a plain
        #      two-colour test image showed the same crop). This turned out NOT to be
        #      a cartopy/warp-specific bug -- see the set_aspect() comment below the
        #      final imshow() call -- so it isn't actually why (3) was dropped; it's
        #      dropped because going through cartopy's transform/PROJ warp machinery
        #      for a plain equirectangular->Mercator reprojection is unnecessary
        #      complexity and overhead once a closed-form pre-warp is available (below).
        # Since Plot's axes are already in Web Mercator (WEB_MERCATOR), and Mercator's
        # X is just linearly-scaled longitude while its Y is a closed-form function of
        # latitude (y = R*ln(tan(pi/4 + lat/2)), verified live to match
        # ccrs.Mercator.GOOGLE's own projection to 6 decimal places), the data can be
        # pre-warped to the axes' native (projected) coordinates directly in numpy --
        # no PROJ/cartopy transform involved at all -- then handed to a plain
        # (non-transform) imshow(), which is a simple, fast, reliable 2D array resize
        # with no warping bugs and no gouraud dithering. Rows within
        # +-MERCATOR_LAT_LIMIT keep is a hard requirement here (not just an
        # optimisation): the poles are a genuine singularity in the Mercator Y
        # formula, and included poles would also collide after clamping, breaking
        # RegularGridInterpolator's strictly-ascending-axis requirement.
        merc_x = _MERCATOR_R * np.radians(new_lons)
        valid_lat_rows = np.abs(new_lats) <= MERCATOR_LAT_LIMIT
        lats_valid = new_lats[valid_lat_rows]
        merc_y = _MERCATOR_R * np.log(np.tan(np.pi / 4 + np.radians(lats_valid) / 2))

        interp = RegularGridInterpolator(
            (merc_y, merc_x), rgba[valid_lat_rows], bounds_error=False, fill_value=0.0
        )
        uniform_merc_y = np.linspace(merc_y.min(), merc_y.max(), len(lats_valid))
        mesh_merc_y, mesh_merc_x = np.meshgrid(uniform_merc_y, merc_x, indexing="ij")
        rgba_merc = interp((mesh_merc_y, mesh_merc_x))

        plot = Plot(self.map_data.region)
        plot.get_figure()
        plot.ax.imshow(
            rgba_merc,
            extent=[merc_x.min(), merc_x.max(), uniform_merc_y.min(), uniform_merc_y.max()],
            origin="lower",  # regrid_for_lod always returns new_lats ascending (south-first)
            interpolation="bilinear",
            zorder=2,
        )
        # imshow() resets the axes' aspect to 1.0 ("equal") as a side effect, silently
        # overriding get_figure()'s set_aspect("auto") -- with adjustable="box" still
        # in effect from get_figure(), that shrinks the actual drawn viewport to fit a
        # 1:1 data-unit box inside the figure's 2:1 (width:height) canvas, letterboxing
        # the render into roughly the centre HALF of the image. Confirmed live: this
        # reproduces even calling the plain (non-cartopy) matplotlib Axes.imshow
        # directly, with fully-opaque OR sparse/transparent data, and with or without a
        # transform= -- it's a plain imshow/aspect interaction, not anything specific
        # to cartopy, Mercator, or this layer's RGBA/alpha approach. Re-asserting
        # "auto" immediately after imshow() restores the full-canvas viewport.
        plot.ax.set_aspect("auto", adjustable="box")
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
