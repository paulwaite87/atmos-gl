#!/usr/bin/env python3
import gc
import logging
import os

import cartopy.crs as ccrs
import matplotlib as mpl
import matplotlib.colors as mcolors
import numpy as np

from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.coastline import coastline_land_mask, gshhg_land_feature
from atmos_gl.lib.greenhouse_gases import (
    SPECIES,
    camsforecast_cache_path,
    compute_anomaly,
    egg4_baseline_cache_path,
    resolve_baseline_year,
)
from atmos_gl.lib.netcdf_field import load_field
from .common import Updater, MapData
from .plotting import Plot, clamp_lats_to_mercator_limit

logger = logging.getLogger(__name__)

# CAMS's high-resolution forecast is ~9km (~0.1 deg) native, but rendering AT that
# resolution is far too slow to be practical: live timing found pcolormesh alone
# taking >80s per render at the native 6.5M-point grid (regrid+coastline-mask
# together are a comparatively cheap ~7s) -- with 4 species x mode combinations
# rendered every cycle, that's minutes per cycle just for this one layer. 0.25 deg
# (matching this codebase's "low" LOD tier default) cuts the point count by ~6x,
# bringing pcolormesh back into the same ballpark as every other layer.
_REGRID_STEP_DEG = 0.25

_LAND_TINT_COLOR = "#5a5a5a"

# Both CAMS datasets (the current forecast and the EGG4 baseline) use the same
# in-file netCDF variable names for these two species -- confirmed by downloading and
# inspecting real files from both datasets (see the published spec's issue comments).
# NOT the same as the request-time CDS variable identifiers
# (co2_column_mean_molar_fraction/ch4_column_mean_molar_fraction, used in
# collectors/greenhouse_gases.py's request builders) -- ECMWF's GRIB-to-netCDF
# conversion exposes them under their short GRIB_cfVarName instead. Units are
# confirmed directly from the files' own `units` attribute: ppm/ppb, exactly the
# display units this layer wants, so no conversion factor is needed.
_CAMS_VARS = {"co2": "tcco2", "ch4": "tcch4"}
_PALETTES = {"thermal": "magma", "vivid": "turbo", "deep": "viridis", "ocean": "inferno"}
# Flat, species-prefixed setting keys (co2_min_ppm, ch4_palette, ...) rather than a
# nested co2/ch4 sub-dict -- FIELD_SPECS/validate_against_specs (routes/field_specs.py)
# only understands flat (section, option) keys, matching every other section's
# settings shape (e.g. sst.min_c, waves.min_wave_height), so per-species settings stay
# flat with a species prefix instead of introducing nested-dict config support.
_SCALE_SETTING_KEYS = {"co2": ("co2_min_ppm", "co2_max_ppm"), "ch4": ("ch4_min_ppb", "ch4_max_ppb")}


class GhgUpdater(Updater):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "greenhouse_gases", map_data)
        self.species = self.settings.get("species", "co2").strip().lower()
        self.mode = self.settings.get("mode", "absolute").strip().lower()

    def _output_path_for(self, species: str, mode: str) -> str:
        """Per-(species, mode), ALWAYS-kept-fresh output path: 'data/greenhouse_gases.png'
        -> e.g. 'data/greenhouse_gases_co2_anomaly.png'. All 4 combinations render here
        every cycle (independent of the configured species/mode) so the frontend can
        switch between them instantly -- see ui/modules/greenhouse_gases.js."""
        base, ext = os.path.splitext(self.output_path)
        return f"{base}_{species}_{mode}{ext}"

    def plot(self, species: str, mode: str, current_nc: str, egg4_nc: str | None, output_path: str):
        alpha = float(self.settings.get("opacity", 60) / 100)

        display_data, lat_raw, lon_norm = load_field(current_nc, _CAMS_VARS[species])

        if mode == "anomaly":
            baseline_matrix, baseline_lat, baseline_lon = load_field(
                egg4_nc, _CAMS_VARS[species], reduce="mean"
            )
            display_data = compute_anomaly(
                display_data, lat_raw, lon_norm, baseline_matrix, baseline_lat, baseline_lon
            )

        new_lats, new_lons, display_data = self.regrid_for_lod(
            display_data, lat_raw, lon_norm, fill_value=np.nan, step_override=_REGRID_STEP_DEG,
        )
        mesh_lon, mesh_lat = np.meshgrid(new_lons, new_lats)
        land = coastline_land_mask(mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 90.0)
        if land is not None and land.shape == display_data.shape:
            display_data[land] = np.nan

        if mode == "anomaly":
            # Auto-scaled from the data (98th percentile of |anomaly|) rather than a
            # manual setting -- anomaly ranges are small and data-dependent enough
            # that a fixed scale would need constant retuning. Same technique
            # SSTUpdater.plot() uses for SST's anomaly mode.
            abs_anomalies = np.abs(display_data)
            calculated_range = (
                float(np.nanpercentile(abs_anomalies, 98))
                if np.any(~np.isnan(abs_anomalies))
                else 1.0
            )
            anomaly_range = max(0.1, calculated_range)
            vmin, vmax = -anomaly_range, anomaly_range
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
            cmap = mpl.cm.get_cmap("coolwarm")
        else:
            min_key, max_key = _SCALE_SETTING_KEYS[species]
            vmin = self.settings.get(min_key, 0)
            vmax = self.settings.get(max_key, 1)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
            palette_key = self.settings.get(f"{species}_palette", "thermal").lower()
            cmap = mpl.cm.get_cmap(_PALETTES.get(palette_key, "magma"))

        plot = Plot(self.map_data.region)
        plot.get_figure()
        # None (geometry unavailable) skips the tint rather than crashing -- same
        # graceful-fallback contract as the mask above.
        land_feature = gshhg_land_feature()
        if land_feature is not None:
            plot.ax.add_feature(
                land_feature,
                facecolor=_LAND_TINT_COLOR,
                edgecolor="none",
                zorder=1,
            )
        plot.ax.pcolormesh(
            new_lons,
            clamp_lats_to_mercator_limit(new_lats),
            display_data,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            norm=norm,
            alpha=alpha,
            shading="nearest",
            rasterized=True,
            zorder=2,
        )
        plot.save_figure(output_path)
        # Legend key renders entirely client-side now (issue #302). Absolute mode's
        # vmin/vmax come straight from settings (co2_min_ppm/co2_max_ppm etc, already
        # visible to the frontend via /api/config); anomaly mode's are auto-scaled from
        # live data (98th percentile, above) and have no other way to reach the
        # frontend, so only anomaly is written -- mirrors SSTUpdater's identical
        # sst_meta.json sidecar for the same "data-dependent range" problem.
        if mode == "anomaly":
            self._write_meta_sidecar(
                "ghg_meta.json", species, {"anomaly": {"vmin": vmin, "vmax": vmax}}
            )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()
        gc.collect()

        logger.debug(f"Successfully rendered {species} {mode} greenhouse gas map.")

    def _mode_settings_signature(self, species: str, mode: str) -> str:
        """Render-relevant settings for (species, mode), for _is_render_fresh --
        opacity and key_fontsize are baked into the rendered pixels for both modes
        (see plot()'s alpha and save_key_image's key_fontsize); min/max/palette only
        apply to absolute (anomaly is auto-scaled from the data). baseline_year is
        included for anomaly even though it also selects a different egg4_nc source
        path -- belt and suspenders, and it's what actually changed from the user's
        perspective if they edit it."""
        values = {
            "opacity": self.settings.get("opacity", 60),
            "key_fontsize": self.common.get("key_fontsize", 10),
        }
        if mode == "absolute":
            min_key, max_key = _SCALE_SETTING_KEYS[species]
            values.update({
                "min": self.settings.get(min_key, 0),
                "max": self.settings.get(max_key, 1),
                "palette": self.settings.get(f"{species}_palette", "thermal"),
            })
        else:
            values["baseline_year"] = resolve_baseline_year(self.settings)
        return self._settings_signature(values)

    def run(self, max_hours=None):
        # max_hours is a no-op here -- GHG renders once per cycle per (species, mode),
        # not per forecast hour. Accepted only so layer_builder's dispatch can call
        # every TASK_CLASSES entry's run() the same way.
        current_nc = camsforecast_cache_path(self.workdir)
        if not os.path.exists(current_nc):
            logger.info(
                "Greenhouse gases: CAMS forecast cache not present yet "
                "(data collector hasn't fetched it); skipping."
            )
            return

        baseline_year = resolve_baseline_year(self.settings)
        egg4_nc = egg4_baseline_cache_path(self.workdir, baseline_year)
        egg4_available = os.path.exists(egg4_nc)

        for species in SPECIES:
            for mode in ("absolute", "anomaly"):
                if mode == "anomaly" and not egg4_available:
                    logger.debug(
                        f"Greenhouse gases: EGG4 baseline {baseline_year} not present "
                        f"yet; skipping {species} anomaly."
                    )
                    continue

                out = self._output_path_for(species, mode)
                sources = [current_nc] + ([egg4_nc] if mode == "anomaly" else [])
                sig = self._mode_settings_signature(species, mode)
                fresh = self._is_render_fresh(out, sources, sig)
                if not fresh:
                    logger.info(f"Generating greenhouse gases {species} {mode} plot...")
                    self.plot(
                        species, mode, current_nc, egg4_nc if mode == "anomaly" else None, out
                    )
                    self._write_render_signature(out, sig)

        # Publish whichever (species, mode) is currently configured -- unconditionally
        # of whether it needed re-rendering above, same as SSTUpdater.run(). Skipped
        # only if the configured mode is anomaly and the baseline isn't cached yet
        # (nothing was rendered for it this cycle or any prior one).
        if self.mode == "anomaly" and not egg4_available:
            return

        self._publish_variant(self._output_path_for(self.species, self.mode))
