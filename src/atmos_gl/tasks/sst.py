#!/usr/bin/env python3
import os
import shutil
import logging
import gc
import numpy as np
import xarray as xr
import matplotlib as mpl
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import cartopy.feature as cfeature
from scipy.ndimage import distance_transform_edt

# Internal imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.coastline import coastline_land_mask
from .common import Updater, MapData
from .plotting import Plot

logger = logging.getLogger(__name__)

# Fixed regrid step for SST's coastline-crispness pass -- finer than any regrid_for_lod
# LOD tier gives (needed since OISST's native 0.25 deg/~28km grid is much coarser than
# other fields), not a user setting (SST's raw data doesn't warrant detail control).
# Chosen empirically: at world scale 0.05 deg (~5.6km) took ~4 minutes for regrid+mask
# alone (25.9M points) -- impractical for a periodic render. 0.08 deg (~8.9km) completes
# a full render (regrid+mask+pcolormesh+savefig) in ~45s, comparable to the old 0.15 deg
# tier's production timing, while resolving roughly twice as fine.
_SST_REGRID_STEP_DEG = 0.08

# Land tint drawn beneath the data (see plot()) so the coastline reads clearly
# regardless of the active colormap -- anomaly mode's coolwarm renders near-zero
# values close to white, which is visually indistinguishable from bare (transparent)
# land without this. A neutral, unsaturated gray reads as "land" against both magma
# (absolute) and coolwarm (anomaly).
_LAND_TINT_COLOR = "#5a5a5a"


class SSTUpdater(Updater):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "sst", map_data)
        self.mode = self.settings.get("mode", "absolute").strip().lower()

    def _output_path_for_mode(self, mode: str) -> str:
        """Per-mode, ALWAYS-kept-fresh output path: 'data/sst.png' -> e.g.
        'data/sst_anomaly.png'. Both modes render here every cycle (independent of
        the configured `mode`) so the frontend can switch between them instantly --
        see ui/modules/sst.js."""
        base, ext = os.path.splitext(self.output_path)
        return f"{base}_{mode}{ext}"

    def plot(self, mode: str, nc_path: str, output_path: str):
        alpha = float(self.settings.get("opacity", 40) / 100)

        # --- Data Loading ---
        ds = xr.open_dataset(nc_path, chunks={"time": 1})
        latest_slice = ds.isel(time=-1)

        lat_raw = latest_slice["lat"].values
        lon_raw = latest_slice["lon"].values

        # Dynamically target 'anom' for anomaly mode, or 'sst' for absolute mean mode
        data_var = "anom" if mode == "anomaly" else "sst"
        raw_matrix = latest_slice[data_var].values.squeeze()

        # Cleanly transform NOAA's 0-360 range into a standard -180 to +180 baseline
        lon_norm = ((lon_raw + 180) % 360) - 180

        # Sort along longitudes to avoid geometric rendering seams or distortions
        lon_sort_idx = np.argsort(lon_norm)
        lon_norm = lon_norm[lon_sort_idx]
        raw_matrix = raw_matrix[:, lon_sort_idx]

        ds.close()
        del ds
        gc.collect()

        # Nearest-fill NaN (native OISST land cells) before regridding -- same technique
        # raster_tiles.bake_field uses for waves/wind tiles -- so the interpolation below
        # doesn't blur a blank halo around the coast; the true coastline geometry below
        # is what actually determines the rendered land/sea boundary, not this fill.
        raw_matrix = np.asarray(raw_matrix, dtype=np.float64)
        bad = ~np.isfinite(raw_matrix)
        if bad.any() and not bad.all():
            idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
            raw_matrix = raw_matrix[tuple(idx)]

        # LOD regrid off OISST's coarse native 0.25 deg grid down to _SST_REGRID_STEP_DEG,
        # then cut the true coastline the same way currents.py does -- see
        # docs/adr/0004-render-bbox-clipping-is-dead-code.md.
        new_lats, new_lons, display_data = self.regrid_for_lod(
            raw_matrix, lat_raw, lon_norm, fill_value=np.nan,
            step_override=_SST_REGRID_STEP_DEG,
        )
        mesh_lon, mesh_lat = np.meshgrid(new_lons, new_lats)
        land = coastline_land_mask(
            mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 90.0, res="50m"
        )
        if land is not None and land.shape == display_data.shape:
            display_data[land] = np.nan

        # --- Dynamic Mode Styling Pipeline ---
        if mode == "anomaly":
            # Isolates 98th percentile of absolute variance on screen for stable scale bounds
            abs_anomalies = np.abs(display_data)
            calculated_range = (
                float(np.nanpercentile(abs_anomalies, 98))
                if np.any(~np.isnan(abs_anomalies))
                else 4.0
            )
            anomaly_range = max(0.5, calculated_range)

            vmin, vmax = -anomaly_range, anomaly_range
            norm = mcolors.TwoSlopeNorm(vmin=vmin, vcenter=0, vmax=vmax)
            cmap = mpl.cm.get_cmap("coolwarm")
            title_text = "SST Climatological Anomaly (°C)"
            tick_format = "%.1f"
        else:
            # Absolute Mode Configurations
            vmin = self.settings.get("min_c", 0)
            vmax = self.settings.get("max_c", 32)
            norm = mcolors.Normalize(vmin=vmin, vmax=vmax)

            palette_key = self.settings.get("palette", "thermal").lower()
            palettes = {
                "thermal": "magma",
                "vivid": "turbo",
                "deep": "viridis",
                "ocean": "inferno",
            }
            cmap = mpl.cm.get_cmap(palettes.get(palette_key, "magma"))
            title_text = "Sea Surface Temp (°C)"
            tick_format = "%d"

        # --- Canvas Initialization ---
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # Flat land tint UNDER the data (zorder below the pcolormesh's) so masked
        # (transparent) cells read as clearly "land" regardless of what colour the
        # nearby ocean data happens to be -- see _LAND_TINT_COLOR.
        plot.ax.add_feature(
            cfeature.NaturalEarthFeature("physical", "land", "50m"),
            facecolor=_LAND_TINT_COLOR,
            edgecolor="none",
            zorder=1,
        )

        # Render complete mapped geographic array using exact pixel cell boundaries
        plot.ax.pcolormesh(
            new_lons,
            new_lats,
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
        calculated_ticks = np.linspace(vmin, vmax, 5)
        self.save_key_image(
            output_path,
            cmap,
            norm,
            calculated_ticks,
            title_text,
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            tick_format=tick_format,
            weight="bold",
        )

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug(f"Successfully rendered raw NOAA OISST map in {mode} mode.")

    def _publish_current_mode(self, mode_output_path: str):
        """Copy the currently-configured mode's per-mode render to the stable,
        run-agnostic base filename (sst.png/sst_key.png) for anything still reading
        that name directly. Always refreshed each cycle (a cheap file copy),
        independent of whether that mode's plot needed re-rendering this cycle."""
        base, ext = os.path.splitext(self.output_path)
        mode_base, mode_ext = os.path.splitext(mode_output_path)
        pairs = [
            (mode_output_path, self.output_path),
            (f"{mode_base}_key{mode_ext}", f"{base}_key{ext}"),
        ]
        for src, dst in pairs:
            if not os.path.exists(src):
                continue
            tmp = f"{dst}.tmp"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)

    def run(self, max_hours=None):
        # max_hours is a no-op here -- SST renders once per cycle, not per forecast
        # hour, so it has nothing to cap. Accepted only so layer_builder's dispatch can
        # call every TASK_CLASSES entry's run() the same way.
        # The data_collector fetches BOTH modes' netCDFs unconditionally (see
        # collectors/sst.py), so both are rendered here every cycle too -- each to its
        # own permanent output path, independent of which mode is currently configured
        # -- so the frontend can switch between them instantly (ui/modules/sst.js)
        # rather than waiting on a config change + re-render round-trip.
        from atmos_gl.lib.oisst import oisst_cache_path, OISST_MODES

        for mode in OISST_MODES:
            nc_path = oisst_cache_path(self.workdir, mode)
            if not os.path.exists(nc_path):
                logger.info(
                    f"SST: cache {os.path.basename(nc_path)} not present yet "
                    f"(data collector hasn't fetched it); skipping {mode}."
                )
                continue

            out = self._output_path_for_mode(mode)
            fresh = os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(nc_path)
            if not fresh:
                logger.info(f"Generating SST {mode} plot...")
                self.plot(mode, nc_path, out)

            if mode == self.mode:
                self._publish_current_mode(out)
