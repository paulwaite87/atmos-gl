#!/usr/bin/env python3
import os
import logging
import warnings
import requests
import numpy as np
import xarray as xr
import matplotlib.colors as mcolors
import cartopy.crs as ccrs

from datetime import datetime, timedelta, timezone
import gc

# Internal imports
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, Plot

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)


class CurrentsUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Currents", map_data)
        self.set_output_path()
        self.nc_path = os.path.join(self.workdir, "data/rtofs_currents.nc")

        # Local paths for the static background elements
        self.shape_dir = os.path.join(self.workdir, "assets/shapes")
        self.shape_base = os.path.join(self.shape_dir, "ne_10m_ocean_current_lines.shp")

        # REWORKED HIGH-VISIBILITY BASE COLORS (RGB formats only)
        # Alpha channels are dynamically attached during rendering based on settings!
        self.PALETTES = {
            "thermal_red": [
                (0.65, 0.0, 0.0),  # Crimson (Slow)
                (1.0, 0.25, 0.0),  # Vivid Orange (Medium)
                (1.0, 0.85, 0.0),  # Neon Yellow (Fast)
                (1.0, 1.0, 1.0)  # Blinding White (Ultra-Fast)
            ],
            "electric_blue": [
                (0.0, 0.35, 0.55),  # Deep Cyan (Slow)
                (0.0, 0.85, 1.0),  # Electric Teal (Medium)
                (0.75, 1.0, 1.0)  # Ice White (Fast)
            ],
            "toxic_neon": [
                (0.0, 0.45, 0.15),  # Dark Lime (Slow)
                (0.25, 1.0, 0.0),  # Neon Green (Medium)
                (0.95, 1.0, 0.3)  # Sulfur Yellow (Fast)
            ],
            "cyberpunk": [
                (0.45, 0.0, 0.45),  # Deep Magenta (Slow)
                (1.0, 0.0, 0.55),  # Hot Pink (Medium)
                (0.0, 1.0, 0.75)  # Electric Turquoise (Fast)
            ]
        }

    def check_local_assets(self):
        """Determines if target RTOFS data or map line shapes are missing locally,
        returning the active backup URL targets if initialization downloads are required.
        """
        base_url = self.settings.get("url").rstrip('/')
        forecast_hour = self.settings.get("forecast_hour", fallback="024").zfill(3)
        now = datetime.now(timezone.utc)

        nc_url = None
        nc_needed = not os.path.exists(self.nc_path)
        shapes_needed = self.settings.getboolean("show_fixed_currents", fallback=False) and not os.path.exists(
            self.shape_base)

        # Generate structural paths for RTOFS fallback loops if files aren't cached
        if nc_needed:
            for day_offset in range(3):
                date_str = (now - timedelta(days=day_offset)).strftime("%Y%m%d")
                urls_to_try = [
                    f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_f{forecast_hour}_prog.nc",
                    f"{base_url}/rtofs.{date_str}/rtofs_glo_2ds_n000_prog.nc"
                ]

                for url in urls_to_try:
                    try:
                        # Fall back to a lightweight verification to avoid scanning dead day folders
                        res = requests.head(url, timeout=5)
                        if res.status_code == 200:
                            nc_url = url
                            break
                    except requests.RequestException:
                        continue
                if nc_url:
                    break

            if not nc_url:
                raise RuntimeError("Could not find any fallback RTOFS ocean model cycles online.")

        return nc_url, nc_needed, shapes_needed

    def download_data(self, url):
        """Downloads the NetCDF file via streaming chunks."""
        logger.info(f"Downloading RTOFS data matrix from {url}...")
        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()

        os.makedirs(os.path.dirname(self.nc_path), exist_ok=True)
        with open(self.nc_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)

    def download_static_shapes(self):
        """Downloads the baseline atlas map pathways if missing from asset collections."""
        remote_shapes_url = self.settings.get("remote_shapes_url", fallback=None)
        if not remote_shapes_url:
            logger.warning("Fixed current lines requested, but 'remote_shapes_url' is missing from configuration.")
            return

        logger.info(f"Downloading baseline atlas vectors from {remote_shapes_url}...")
        r = requests.get(remote_shapes_url, timeout=120)
        r.raise_for_status()

        os.makedirs(self.shape_dir, exist_ok=True)
        ext = os.path.splitext(remote_shapes_url)[1].lower()
        target_file = self.shape_base if ext == ".shp" else os.path.join(self.shape_dir,
                                                                         f"ne_10m_ocean_current_lines{ext}")

        with open(target_file, "wb") as f:
            f.write(r.content)

    def plot(self):
        """Renders ocean currents with adaptive density, dynamic line widths,
        high-resolution land masking, and a configurable global width multiplier.
        Includes options for a toggleable fixed background pathway overlay.
        """
        import matplotlib.pyplot as plt
        import cartopy.io.shapereader as shapereader
        from scipy.interpolate import griddata, NearestNDInterpolator

        logger.debug(f"Plotting Ocean Currents for {self.map_data.region.region_identifier}")

        palette_name = self.settings.get("palette", fallback="thermal_red")
        if palette_name not in self.PALETTES:
            palette_name = "thermal_red"

        # User configurations
        alpha_setting = self.settings.getfloat("alpha", fallback=1.0)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        width_factor = self.settings.getfloat("width_factor", fallback=1.0)
        width_factor = max(0.1, width_factor)

        show_fixed_currents = self.settings.getboolean("show_fixed_currents", fallback=False)
        key_position = self.settings.get("key_position", fallback="bottom-right").strip().lower()
        key_fontsize = self.settings.getint("key_fontsize", fallback=10)

        # 1. Initialize Canvas Early
        plot = Plot(self.map_data.region)
        plot.get_figure()

        # 2. OPTIONAL FIXED HISTORICAL CONVEYOR BELT OVERLAY
        if show_fixed_currents and os.path.exists(self.shape_base):
            try:
                logger.debug("Injecting fixed ocean conveyor belt vectors from shape definitions...")
                reader = shapereader.Reader(self.shape_base)
                for record in reader.records():
                    curr_type = record.attributes.get('type', 'Warm')
                    line_color = '#FF5555' if curr_type == 'Warm' else '#5599FF'

                    plot.ax.add_geometries(
                        [record.geometry],
                        crs=ccrs.PlateCarree(),
                        facecolor='none',
                        edgecolor=line_color,
                        linewidth=1.4,
                        alpha=0.45,
                        zorder=2
                    )
            except Exception as ex:
                logger.error(f"Failed to cleanly project fixed current shapes: {ex}")

        # 3. Load Live NetCDF Dataset
        ds = xr.open_dataset(self.nc_path)

        lon_raw = ((ds["Longitude"].values + 180) % 360) - 180
        lat_raw = ds["Latitude"].values
        lon_min, lat_min, lon_max, lat_max = self.map_region_bbox
        buf = 1.5

        # Extract index bounds via mask
        mask = (
                (lon_raw >= lon_min - buf) & (lon_raw <= lon_max + buf) &
                (lat_raw >= lat_min - buf) & (lat_raw <= lat_max + buf)
        )

        if not np.any(mask):
            ds.close()
            raise ValueError(f"No ocean current data points found inside bbox.")

        y_indices, x_indices = np.where(mask)
        raw_height = y_indices.max() - y_indices.min() + 1
        raw_width = x_indices.max() - x_indices.min() + 1

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

        # 4. Filter Land Mass NaNs for velocity vector calculations
        valid = ~np.isnan(u_raw) & ~np.isnan(v_raw)
        if not np.any(valid):
            logger.warning("No open water points found in this region slice.")
            plot.save_figure(self.output_path)
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        u_points = u_raw[valid] * 1.94384  # Convert m/s to Knots
        v_points = v_raw[valid] * 1.94384

        # 5. Generate perfectly uniform grid and interpolate velocities
        grid_lon = np.linspace(lon_min, lon_max, 200)
        grid_lat = np.linspace(lat_min, lat_max, 200)
        mesh_lon, mesh_lat = np.meshgrid(grid_lon, grid_lat)

        u_grid = griddata(points, u_points, (mesh_lon, mesh_lat), method='linear', fill_value=np.nan)
        v_grid = griddata(points, v_points, (mesh_lon, mesh_lat), method='linear', fill_value=np.nan)

        # --- LAND MASK FIX ENGINE ---
        raw_land_mask = np.isnan(u_raw)
        all_raw_points = np.column_stack((lons_clipped.ravel(), lats_clipped.ravel()))
        all_raw_land_states = raw_land_mask.ravel()

        mask_interpolator = NearestNDInterpolator(all_raw_points, all_raw_land_states)
        grid_land_mask = mask_interpolator(mesh_lon, mesh_lat).astype(bool)

        u_grid[grid_land_mask] = np.nan
        v_grid[grid_land_mask] = np.nan
        # ----------------------------

        speed_grid = np.sqrt(u_grid ** 2 + v_grid ** 2)

        # 6. Dynamic Color Normalization Range
        vmax_dynamic = max(1.5, float(np.nanpercentile(speed_grid, 95)))
        norm = mcolors.Normalize(vmin=0.0, vmax=vmax_dynamic)

        # 7. AUTOMATED DENSITY & WIDTH CALCULATION ENGINE
        geo_span = max(abs(lon_max - lon_min), abs(lat_max - lat_min))
        fig_w_inches, _ = plot.fig.get_size_inches()
        canvas_pixel_width = fig_w_inches * plot.fig.dpi

        if geo_span >= 40.0:
            base_density = 0.65
        elif geo_span <= 2.0:
            base_density = 1.65
        else:
            base_density = 1.65 - ((geo_span - 2.0) / (40.0 - 2.0)) * (1.65 - 0.65)

        res_factor = canvas_pixel_width / 1200.0
        res_factor = np.clip(res_factor, 0.8, 1.5)
        calculated_density = base_density * res_factor

        max_thickness = 4.8 if geo_span < 10.0 else 3.8
        min_thickness = 1.8 if geo_span < 10.0 else 1.2

        speed_ratio = np.clip(speed_grid / vmax_dynamic, 0.0, 1.0)
        base_linewidth = min_thickness + (speed_ratio * (max_thickness - min_thickness))
        calculated_linewidth = base_linewidth * width_factor

        base_arrowsize = 1.5 if geo_span < 10.0 else 1.1
        calculated_arrowsize = base_arrowsize * max(1.0, width_factor * 0.85)

        logger.debug(
            f"Dynamic Tuning -> Scale: {geo_span:.1f}° | Density: {calculated_density:.2f} | "
            f"Width Factor: {width_factor}x"
        )

        # 8. Render Streamlines
        custom_rgba_list = [(r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]]
        cmap = mcolors.LinearSegmentedColormap.from_list("current_speed", custom_rgba_list, N=256)

        strm = plot.ax.streamplot(
            grid_lon, grid_lat, u_grid, v_grid,
            color=speed_grid,
            cmap=cmap,
            norm=norm,
            linewidth=calculated_linewidth,
            density=calculated_density,
            arrowstyle='->',
            arrowsize=calculated_arrowsize,
            transform=ccrs.PlateCarree(),
            zorder=3
        )

        # 9. ENHANCEMENT: TEXT-SAFE PADDED INSET COLOR KEY
        position_map = {
            "top-left": [0.04, 0.89, 0.28, 0.03],
            "top-right": [0.68, 0.89, 0.28, 0.03],
            "bottom-left": [0.04, 0.08, 0.28, 0.03],
            "bottom-right": [0.68, 0.08, 0.28, 0.03]
        }

        bbox_coords = position_map.get(key_position, position_map["bottom-right"])
        cbar_ax = plot.ax.inset_axes(bbox_coords, transform=plot.ax.transAxes)

        cbar_ax.patch.set_facecolor('#111111')
        cbar_ax.patch.set_alpha(0.4)

        calculated_ticks = np.linspace(0.0, vmax_dynamic, 4)

        cbar = plt.colorbar(
            strm.lines,
            cax=cbar_ax,
            orientation='horizontal',
            ticks=calculated_ticks
        )

        cbar.ax.xaxis.set_tick_params(color='white', labelsize=key_fontsize, labelcolor='white', pad=3)
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter('%.1f'))
        cbar.outline.set_edgecolor('white')
        cbar.outline.set_linewidth(0.5)
        cbar.ax.set_title("Current Speed (knots)", color='white', fontsize=key_fontsize, pad=5, weight='bold')

        plot.save_figure(self.output_path)

        plt_close = getattr(plot, 'close', None)
        if callable(plt_close):
            plt_close()

        logger.debug(f"Finished Currents plot. Memory cleared.")

    def run(self):
        self.exit_if_disabled()
        try:
            url, nc_needed, shapes_needed = self.check_local_assets()

            if shapes_needed:
                self.download_static_shapes()

            if nc_needed and url:
                self.download_data(url)

            # Generate if data was freshly downloaded, the output graphic is missing, or style profiles changed
            if nc_needed or shapes_needed or not os.path.exists(self.output_path) or self.config.has_changed:
                logger.info("Generating Currents layer...")
                self.plot()
        except Exception as e:
            logger.exception(f"Currents update failed: {e}")