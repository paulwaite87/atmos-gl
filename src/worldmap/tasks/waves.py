#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs
import gc

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class WavesUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Waves", map_data)
        self.set_output_path()

        # DESIGNED GRADIENTS FOR WAVE HEIGHT INTENSITY
        self.PALETTES = {
            "ocean_storm": [
                (0.0, 0.2, 0.4),  # Calm: Deep Blue
                (0.0, 0.6, 0.3),  # Low: Emerald Teal
                (0.9, 0.7, 0.0),  # Moderate: Amber Yellow
                (0.8, 0.2, 0.0),  # Heavy: Crimson Red
                (0.9, 0.9, 0.9),  # Extreme: Foam White
            ],
            "neon_surge": [
                (0.0, 0.8, 1.0),  # 0m+: Electric Cyan
                (0.0, 0.95, 0.4),  # Low Swell: Neon Green
                (1.0, 0.9, 0.0),  # Moderate: Vivid Yellow
                (1.0, 0.3, 0.0),  # Heavy: Bright Orange
                (0.9, 0.0, 0.5),  # Violent: Hot Magenta
                (0.6, 0.0, 0.7),  # Extreme: Deep Purple
            ],
            "solar_flare": [
                (0.6, 1.0, 0.9),  # 0m+: Soft, glowing cyan (Calm)
                (0.0, 1.0, 0.0),  # Low Swell: Electric Lime
                (1.0, 1.0, 0.0),  # Light Seas: Pure, Blazing Yellow
                (1.0, 0.65, 0.0),  # Moderate: Pierce Orange
                (1.0, 0.2, 0.1),  # Heavy: Safety Red
                (1.0, 0.0, 1.0),  # Extreme: Hot Magenta/Pink
            ],
        }

    def plot(self):
        """Plots an underlying significant wave height contour heatmap
        with adaptive directional quiver arrows layered over top.
        """
        import matplotlib.pyplot as plt
        from scipy.interpolate import griddata, NearestNDInterpolator

        logger.debug(
            f"Plotting Sea Conditions for {self.map_data.region.region_identifier}"
        )

        palette_name = self.settings.get("palette", fallback="ocean_storm")
        if palette_name not in self.PALETTES:
            palette_name = "ocean_storm"

        alpha_setting = self.settings.getfloat("alpha", fallback=0.75)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        # Parse layout configurations
        show_arrows = self.settings.getboolean("show_arrows", fallback=True)
        arrow_density_mod = self.settings.getfloat("arrow_density", fallback=1.0)
        arrow_scale_mod = self.settings.getfloat("arrow_scale", fallback=1.0)
        arrow_scale_mod = max(0.1, arrow_scale_mod)

        key_position = (
            self.settings.get("key_position", fallback="bottom-right").strip().lower()
        )
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)

        # 1. Open Dataset with cfgrib engine backend
        ds = xr.open_dataset(
            self.grib_path,
            engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}},
        )

        lon_raw = ((ds["longitude"].values + 180) % 360) - 180
        lat_raw = ds["latitude"].values
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.0

        lon_inside = (lon_raw >= lon_min - buf) & (lon_raw <= lon_max + buf)
        lat_inside = (lat_raw >= lat_min - buf) & (lat_raw <= lat_max + buf)

        direction_key = "dirpw" if "dirpw" in ds else "mwd"

        if lon_raw.ndim == 1 and lat_raw.ndim == 1:
            spatial_mask = lat_inside[:, np.newaxis] & lon_inside[np.newaxis, :]
            mesh_lon_raw, mesh_lat_raw = np.meshgrid(lon_raw, lat_raw)

            swh_raw = ds["swh"].values[spatial_mask]
            mwd_raw = ds[direction_key].values[spatial_mask]
            lons_clipped = mesh_lon_raw[spatial_mask]
            lats_clipped = mesh_lat_raw[spatial_mask]
        else:
            mask = lon_inside & lat_inside
            swh_raw = ds["swh"].values[mask]
            mwd_raw = ds[direction_key].values[mask]
            lons_clipped = lon_raw[mask]
            lats_clipped = lat_raw[mask]

        ds.close()
        del ds
        gc.collect()

        # 2. Extract valid water data points
        valid = ~np.isnan(swh_raw) & ~np.isnan(mwd_raw)
        if not np.any(valid):
            logger.warning("No open water coordinates found within the region slice.")
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        swh_points = swh_raw[valid]
        mwd_points = mwd_raw[valid]

        # 3. Build high-fidelity unified processing grid mesh
        grid_lon = np.linspace(lon_min, lon_max, 300)
        grid_lat = np.linspace(lat_min, lat_max, 300)
        mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

        rad_angles = np.radians(mwd_points)
        u_points = np.sin(rad_angles)
        v_points = np.cos(rad_angles)

        combined_values = np.column_stack((swh_points, u_points, v_points))
        combined_grid = griddata(
            points,
            combined_values,
            (mesh_lon, mesh_lat),
            method="linear",
            fill_value=np.nan,
        )

        swh_grid = combined_grid[:, :, 0]
        u_grid = combined_grid[:, :, 1]
        v_grid = combined_grid[:, :, 2]

        # --- HIGH RESOLUTION LAND BOUNDARY RECOVERY ---
        raw_land_mask = np.isnan(swh_raw)
        all_raw_points = np.column_stack((lons_clipped.ravel(), lats_clipped.ravel()))
        all_raw_land_states = raw_land_mask.ravel()

        mask_interpolator = NearestNDInterpolator(all_raw_points, all_raw_land_states)
        grid_land_mask = mask_interpolator(mesh_lon, mesh_lat).astype(bool)

        swh_grid[grid_land_mask] = np.nan
        u_grid[grid_land_mask] = np.nan
        v_grid[grid_land_mask] = np.nan
        # -----------------------------------------------

        # 4. Initialize Core Canvas
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # 5. Render Wave Height Contour Heatmap
        custom_rgba_list = [
            (r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]
        ]
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "wave_height", custom_rgba_list, N=256
        )

        levels = np.linspace(0.0, 8.0, 17)
        norm = mcolors.Normalize(vmin=0.0, vmax=8.0)

        cf = plot.ax.contourf(
            grid_lon,
            grid_lat,
            swh_grid,
            levels=levels,
            cmap=cmap,
            norm=norm,
            extend="max",
            antialiased=True,
            transform=ccrs.PlateCarree(),
            zorder=2,
        )

        # 6. ENHANCEMENT: CONDITIONAL ARROW OVERLAY PROJECTION
        if show_arrows:
            geo_span = max(abs(lon_max - lon_min), abs(lat_max - lat_min))

            if geo_span >= 60.0:
                base_stride = 24
                base_q_scale = 110.0
            elif geo_span >= 25.0:
                base_stride = 18
                base_q_scale = 84.0
            elif geo_span >= 8.0:
                base_stride = 12
                base_q_scale = 56.0
            else:
                base_stride = 6
                base_q_scale = 36.0

            calculated_stride = max(2, int(base_stride / arrow_density_mod))

            fig_w_inches, _ = plot.fig.get_size_inches()
            canvas_pixel_width = fig_w_inches * plot.fig.dpi
            res_adjustment = max(0.75, min(1.3, canvas_pixel_width / 1200.0))

            final_q_scale = (base_q_scale * res_adjustment) / arrow_scale_mod

            q_lon = mesh_lon[::calculated_stride, ::calculated_stride]
            q_lat = mesh_lat[::calculated_stride, ::calculated_stride]
            q_u = u_grid[::calculated_stride, ::calculated_stride]
            q_v = v_grid[::calculated_stride, ::calculated_stride]

            q_valid = ~np.isnan(q_u) & ~np.isnan(q_v)

            logger.debug(
                f"Dynamic Wave Vectors -> Span: {geo_span:.1f}° | Stride: {calculated_stride} | "
                f"Arrow Scale Denominator: {final_q_scale:.1f}"
            )

            if np.any(q_valid):
                plot.ax.quiver(
                    q_lon[q_valid],
                    q_lat[q_valid],
                    q_u[q_valid],
                    q_v[q_valid],
                    pivot="middle",
                    color="white",
                    edgecolor="black",
                    linewidth=0.6,
                    scale=final_q_scale,
                    width=0.0022 * max(1.0, arrow_scale_mod * 0.75),
                    headwidth=3.2,
                    headlength=3.5,
                    headaxislength=3.0,
                    minshaft=1.5,
                    transform=ccrs.PlateCarree(),
                    zorder=4,
                )
        else:
            logger.debug(
                "Wave vector rendering skipped by user configuration settings."
            )

        # 7. ENHANCEMENT: DYNAMIC ADJUSTED COLOR KEY OVERLAY
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03],
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor("#111111")
        cbar_ax.patch.set_alpha(0.4)

        cbar = plt.colorbar(
            cf, cax=cbar_ax, orientation="horizontal", ticks=[0, 2, 4, 6, 8]
        )

        cbar.ax.xaxis.set_tick_params(
            color="white", labelsize=key_fontsize, labelcolor="white", pad=3
        )
        cbar.outline.set_edgecolor("white")
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title(
            "Wave Height (m)",
            color="white",
            fontsize=key_fontsize,
            pad=5,
            weight="bold",
        )

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Wave condition plotting sequence completed successfully.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = os.path.join(
            self.workdir, f"data/gfs_waves_{self.forecast_hour_str}.grib2"
        )

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/wave/gridded/gfswave.t{self.gfs_run}z.global.0p25.f{self.forecast_hour_str}.grib2"
        if self.remote_data_update(remote_url=url, cache_file_path=self.grib_path):
            logger.info("Generating Waves plot...")
            self.plot()
