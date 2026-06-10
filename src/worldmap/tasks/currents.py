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
from .common import Updater, MapData, Plot, _opaque_cmap

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class CurrentsUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Currents", map_data)

        # HIGH-VISIBILITY BASE COLORS (RGB formats only)
        # Alpha channels are dynamically attached during rendering
        # based on settings!
        self.PALETTES = {
            "thermal_red": [
                (0.65, 0.0, 0.0),  # Crimson (Slow)
                (1.0, 0.25, 0.0),  # Vivid Orange (Medium)
                (1.0, 0.85, 0.0),  # Neon Yellow (Fast)
                (1.0, 1.0, 1.0),  # Blinding White (Ultra-Fast)
            ],
            "electric_blue": [
                (0.0, 0.35, 0.55),  # Deep Cyan (Slow)
                (0.0, 0.85, 1.0),  # Electric Teal (Medium)
                (0.75, 1.0, 1.0),  # Ice White (Fast)
            ],
            "toxic_neon": [
                (0.0, 0.45, 0.15),  # Dark Lime (Slow)
                (0.25, 1.0, 0.0),  # Neon Green (Medium)
                (0.95, 1.0, 0.3),  # Sulfur Yellow (Fast)
            ],
            "cyberpunk": [
                (0.45, 0.0, 0.45),  # Deep Magenta (Slow)
                (1.0, 0.0, 0.55),  # Hot Pink (Medium)
                (0.0, 1.0, 0.75),  # Electric Turquoise (Fast)
            ],
        }

    def save_currents_key(self, output_path, cmap, norm, ticks):
        """Generates a standalone Ocean Currents key image (separate _key.png)."""
        import matplotlib.pyplot as plt
        import matplotlib as mpl

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"
        key_fontsize = self.settings.get("key_fontsize", 10)

        fig, ax = plt.subplots(figsize=(4, 0.3))
        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=_opaque_cmap(cmap)),
            cax=ax,
            orientation="horizontal",
            ticks=ticks,
        )
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter("%.1f"))
        cbar.ax.set_title(
            "Current Speed (m/sec)",
            color="white",
            fontsize=key_fontsize,
            pad=2,
            weight="bold",
        )
        cbar.ax.tick_params(colors="white", labelsize=8)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Currents key to: {key_path}")

    def plot(self):
        """Renders ocean currents with adaptive density, dynamic line widths,
        high-resolution land masking, and a configurable global width multiplier.
        """
        from scipy.interpolate import griddata, NearestNDInterpolator

        logger.debug(
            f"Plotting Ocean Currents for {self.map_data.region.region_identifier}"
        )

        palette_name = self.settings.get("palette", "thermal_red")
        if palette_name not in self.PALETTES:
            palette_name = "thermal_red"

        # User configurable opacity ceiling (1.0 = completely solid neon vectors)
        alpha_setting = self.settings.get("alpha", 1.0)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        # User configurable line weight multiplier (e.g. 1.5 = 150% thicker lines)
        width_factor = self.settings.get("width_factor", 1.0)
        width_factor = max(0.1, width_factor)  # Prevent flat zero or negative scales

        # Load Dataset
        ds = xr.open_dataset(self.nc_path)

        lon_raw = ((ds["Longitude"].values + 180) % 360) - 180
        lat_raw = ds["Latitude"].values
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.5

        # Extract index bounds via mask
        mask = (
            (lon_raw >= lon_min - buf)
            & (lon_raw <= lon_max + buf)
            & (lat_raw >= lat_min - buf)
            & (lat_raw <= lat_max + buf)
        )

        if not np.any(mask):
            ds.close()
            raise ValueError("No ocean current data points found inside bbox.")

        y_indices, x_indices = np.where(mask)
        raw_height = y_indices.max() - y_indices.min() + 1
        raw_width = x_indices.max() - x_indices.min() + 1

        # Dynamic stride targeting ~120 raw points per axis to keep interpolation fast and low-RAM
        stride_y = max(1, raw_height // 120)
        stride_x = max(1, raw_width // 120)

        y_slice = slice(y_indices.min(), y_indices.max() + 1, stride_y)
        x_slice = slice(x_indices.min(), x_indices.max() + 1, stride_x)

        u_raw = ds["u_velocity"].isel(Y=y_slice, X=x_slice).values.squeeze()
        v_raw = ds["v_velocity"].isel(Y=y_slice, X=x_slice).values.squeeze()
        lons_clipped = lon_raw[y_slice, x_slice]
        lats_clipped = lat_raw[y_slice, x_slice]

        ds.close()
        del ds
        gc.collect()

        # Filter Land Mass NaNs for velocity vector calculations
        valid = ~np.isnan(u_raw) & ~np.isnan(v_raw)
        if not np.any(valid):
            logger.warning("No open water points found in this region slice.")
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        u_points = u_raw[valid]
        v_points = v_raw[valid]

        # Generate perfectly uniform grid and interpolate velocities
        grid_lon = np.linspace(lon_min, lon_max, 200)
        grid_lat = np.linspace(lat_min, lat_max, 200)
        mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

        u_grid = griddata(
            points, u_points, (mesh_lon, mesh_lat), method="linear", fill_value=np.nan
        )
        v_grid = griddata(
            points, v_points, (mesh_lon, mesh_lat), method="linear", fill_value=np.nan
        )

        # --- LAND MASK FIX ENGINE ---
        raw_land_mask = np.isnan(u_raw)
        all_raw_points = np.column_stack((lons_clipped.ravel(), lats_clipped.ravel()))
        all_raw_land_states = raw_land_mask.ravel()

        mask_interpolator = NearestNDInterpolator(all_raw_points, all_raw_land_states)
        grid_land_mask = mask_interpolator(mesh_lon, mesh_lat).astype(bool)

        u_grid[grid_land_mask] = np.nan
        v_grid[grid_land_mask] = np.nan

        speed_grid = np.sqrt(u_grid**2 + v_grid**2)

        # Dynamic Color Normalization Range
        vmax_dynamic = max(1.5, float(np.nanpercentile(speed_grid, 95)))
        norm = mcolors.Normalize(vmin=0.0, vmax=vmax_dynamic)

        # 6. Initialize Plotting Configuration
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # AUTOMATED DENSITY & WIDTH CALCULATION ENGINE
        geo_span = max(abs(lon_max - lon_min), abs(lat_max - lat_min))

        fig_w_inches, _ = plot.fig.get_size_inches()
        canvas_pixel_width = fig_w_inches * plot.fig.dpi

        # Calculate Base Density relative to Geographic scale
        if geo_span >= 40.0:
            base_density = 0.65
        elif geo_span <= 2.0:
            base_density = 1.65
        else:
            base_density = 1.65 - ((geo_span - 2.0) / (40.0 - 2.0)) * (1.65 - 0.65)

        # Apply Resolution Scaling Factor
        res_factor = canvas_pixel_width / 1200.0
        res_factor = np.clip(res_factor, 0.8, 1.5)
        calculated_density = base_density * res_factor

        # Calculate Variable Line Weight Matrix based on velocity
        max_thickness = 4.8 if geo_span < 10.0 else 3.8
        min_thickness = 1.8 if geo_span < 10.0 else 1.2

        speed_ratio = np.clip(speed_grid / vmax_dynamic, 0.0, 1.0)
        base_linewidth = min_thickness + (speed_ratio * (max_thickness - min_thickness))

        # Apply the user modifier directly to the calculated width matrix
        calculated_linewidth = base_linewidth * width_factor

        # Adjust arrow size proportionally with the width modifier so they don't look tiny on thick lines
        base_arrowsize = 1.5 if geo_span < 10.0 else 1.1
        calculated_arrowsize = base_arrowsize * max(1.0, width_factor * 0.85)

        logger.debug(
            f"Dynamic Tuning -> Scale: {geo_span:.1f}° | Density: {calculated_density:.2f} | "
            f"Width Factor: {width_factor}x"
        )

        # Render Streamlines with transparency injector
        custom_rgba_list = [
            (r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]
        ]
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "current_speed", custom_rgba_list, N=256
        )

        plot.ax.streamplot(
            grid_lon,
            grid_lat,
            u_grid,
            v_grid,
            color=speed_grid,
            cmap=cmap,
            norm=norm,
            linewidth=calculated_linewidth,
            density=calculated_density,
            arrowstyle="->",
            arrowsize=calculated_arrowsize,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )

        plot.save_figure(self.output_path)
        ticks = np.linspace(0.0, vmax_dynamic, 4)
        self.save_currents_key(self.output_path, cmap, norm, ticks)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Finished Currents plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.nc_path = self.cache_path(f"rtofs_currents_{self.forecast_hour_str}.nc")

        urls_to_try = [
            f"{self.base_url}/rtofs.{self.gfs_date_str}/rtofs_glo_2ds_f{self.forecast_hour_str}_prog.nc",
            f"{self.base_url}/rtofs.{self.gfs_date_str}/rtofs_glo_2ds_n000_prog.nc",
        ]

        # Try above urls, and break on first successful cache download
        for remote_url in urls_to_try:
            if self.remote_data_update(
                remote_url=remote_url, cache_file_path=self.nc_path
            ):
                logger.info("Generating Currents plot...")
                self.plot()
