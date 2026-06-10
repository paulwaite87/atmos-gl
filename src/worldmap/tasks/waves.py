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


class WavesUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Waves", map_data)

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

    def save_waves_key(self, output_path, cmap, norm, threshold=0.0):
        """Generates a standalone Wave Height key image (separate _key.png)."""
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
            ticks=[0, 2, 4, 6, 8],
        )
        title = "Wave Height (m)"
        if threshold > 0.0:
            # Mark the transparent (below-threshold) zone on the key so the ramp's
            # visible start matches what's actually rendered on the map.
            cbar.ax.axvspan(norm.vmin, threshold, color="black", alpha=0.55)
            cbar.ax.axvline(threshold, color="white", linewidth=1.2)
            title = f"Wave Height (m) \u2265 {threshold:g}"
        cbar.ax.set_title(
            title,
            color="white",
            fontsize=key_fontsize,
            pad=2,
            weight="bold",
        )
        cbar.ax.tick_params(colors="white", labelsize=8)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Waves key to: {key_path}")

    # Cache unioned coastline geometry per (resolution, rounded-bbox) so we don't
    # re-read the shapefile and re-union on every scheduled run.
    _coast_cache = {}

    def _coastline_mask(
        self, mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, res
    ):
        """Boolean land mask at grid resolution, cut from true coastline geometry.

        Returns a mesh-shaped boolean array (True over land) using Natural Earth
        'physical/land' polygons at the requested resolution, or None if the
        geometry can't be loaded so the caller can fall back to the data-derived mask.
        """
        try:
            import cartopy.feature as cfeature
            from shapely.ops import unary_union

            key = (
                res,
                round(lon_min, 2),
                round(lat_min, 2),
                round(lon_max, 2),
                round(lat_max, 2),
            )
            land_geom = self._coast_cache.get(key)
            if land_geom is None:
                land = cfeature.NaturalEarthFeature("physical", "land", res)
                geoms = list(
                    land.intersecting_geometries([lon_min, lon_max, lat_min, lat_max])
                )
                if not geoms:
                    # No land in this region -> everything is water.
                    return np.zeros(mesh_lon.shape, dtype=bool)
                land_geom = unary_union(geoms)
                self._coast_cache[key] = land_geom

            try:
                import shapely

                mask = shapely.contains_xy(land_geom, mesh_lon, mesh_lat)
            except (ImportError, AttributeError):
                import shapely.vectorized as shpvec

                mask = shpvec.contains(land_geom, mesh_lon, mesh_lat)
            return np.asarray(mask, dtype=bool)
        except Exception as exc:  # network/data/parse failure -> graceful fallback
            logger.warning(
                f"Coastline geometry unavailable ({exc!r}); "
                "falling back to data-derived land mask."
            )
            return None

    def plot(self):
        """Plots an underlying significant wave height contour heatmap
        with adaptive directional quiver arrows layered over top.
        """
        from scipy.interpolate import griddata, NearestNDInterpolator

        logger.debug(
            f"Plotting Sea Conditions for {self.map_data.region.region_identifier}"
        )

        palette_name = self.settings.get("palette", "ocean_storm")
        if palette_name not in self.PALETTES:
            palette_name = "ocean_storm"

        alpha_setting = self.settings.get("alpha", 0.75)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        # Parse layout configurations
        show_arrows = self.settings.get("show_arrows", True)
        arrow_density_mod = self.settings.get("arrow_density", 1.0)
        arrow_scale_mod = self.settings.get("arrow_scale", 1.0)
        arrow_scale_mod = max(0.1, arrow_scale_mod)

        # Level of detail controls BOTH the processing grid density and the coastline
        # resolution used to cut land out of the field. The wave data itself is 0.25 deg
        # so higher LOD doesn't invent wave detail, but it does sharpen the coastline
        # cutouts (the visibly "blocky" part) from a true coastline geometry.
        try:
            lod = int(self.settings.get("level_of_detail", 2))
        except (TypeError, ValueError):
            lod = 2
        lod = min(3, max(1, lod))
        grid_n = {1: 300, 2: 600, 3: 1100}[lod]
        coast_res = {1: "110m", 2: "50m", 3: "10m"}[lod]

        # Wave-height display threshold: heights below this (in metres) render fully
        # transparent, so calm water shows the base map instead of the lowest colour.
        # 0 disables the threshold (legacy behaviour).
        try:
            wave_threshold = float(self.settings.get("min_wave_height", 0.0))
        except (TypeError, ValueError):
            wave_threshold = 0.0
        wave_threshold = max(0.0, wave_threshold)

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

        # 2. Extract valid water data points.
        # The GFS wave model only solves over open water; land/ice cells come back
        # either as NaN or as a large GRIB fill value (e.g. ~9.999e20) depending on the
        # encoding. cfgrib does not always mask the fill, and an unmasked fill sits above
        # the colour scale -> extend="max" clamps it to the top colour and paints every
        # landmass the maximum-wave-height colour. Treat anything outside the physical
        # wave range as "no data" so land stays transparent however it's encoded.
        SWH_VALID_MAX = (
            60.0  # m: far above any real sea state (~30 m extreme), below any fill
        )
        land_or_missing = (
            ~np.isfinite(swh_raw) | (swh_raw < 0.0) | (swh_raw > SWH_VALID_MAX)
        )
        valid = ~land_or_missing & np.isfinite(mwd_raw)
        if not np.any(valid):
            logger.warning("No open water coordinates found within the region slice.")
            return

        points = np.column_stack((lons_clipped[valid], lats_clipped[valid]))
        swh_points = swh_raw[valid]
        mwd_points = mwd_raw[valid]

        # 3. Build high-fidelity unified processing grid mesh
        grid_lon = np.linspace(lon_min, lon_max, grid_n)
        grid_lat = np.linspace(lat_min, lat_max, grid_n)
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
        # Preferred: cut land using true coastline geometry at the LOD-selected
        # resolution, which gives crisp coastlines independent of the coarse 0.25 deg
        # wave grid. If that geometry can't be loaded (e.g. no network to fetch the
        # Natural Earth data), fall back to the original nearest-neighbour mask derived
        # from the wave data itself, which is blocky but always available.
        grid_land_mask = self._coastline_mask(
            mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, coast_res
        )
        if grid_land_mask is None:
            raw_land_mask = land_or_missing
            all_raw_points = np.column_stack(
                (lons_clipped.ravel(), lats_clipped.ravel())
            )
            all_raw_land_states = raw_land_mask.ravel()
            mask_interpolator = NearestNDInterpolator(
                all_raw_points, all_raw_land_states
            )
            grid_land_mask = mask_interpolator(mesh_lon, mesh_lat).astype(bool)

        swh_grid[grid_land_mask] = np.nan
        u_grid[grid_land_mask] = np.nan
        v_grid[grid_land_mask] = np.nan

        # Apply the display threshold: anything below it becomes transparent, and the
        # matching direction arrows are dropped too so they don't float over the base
        # map. NaN comparisons are False, so land/missing cells are unaffected here.
        if wave_threshold > 0.0:
            with np.errstate(invalid="ignore"):
                below_threshold = swh_grid < wave_threshold
            swh_grid[below_threshold] = np.nan
            u_grid[below_threshold] = np.nan
            v_grid[below_threshold] = np.nan

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

        # NaN cells (land, missing data, below display threshold) render fully
        # transparent rather than the colormap's default.
        cmap.set_bad((0.0, 0.0, 0.0, 0.0))

        norm = mcolors.Normalize(vmin=0.0, vmax=8.0)

        # Smooth continuous blend. The field sits on a regular lon/lat grid, so imshow
        # rasterises it with bilinear interpolation for a seamless min->max gradient.
        # (pcolormesh shading="gouraud" triangulates each quad into two triangles, and
        # with alpha<1 the antialiased triangle edges double-blend into a diagonal
        # "cross-hatch" that becomes visible when zoomed in. imshow has no triangulation,
        # so the colour is genuinely smooth at every zoom.) NaN propagates through the
        # interpolation stencil, so land/threshold edges stay cleanly transparent.
        # Heights above vmax clamp to the palette's top colour.
        plot.ax.imshow(
            swh_grid,
            extent=[grid_lon[0], grid_lon[-1], grid_lat[0], grid_lat[-1]],
            origin="lower",
            interpolation="bilinear",
            cmap=cmap,
            norm=norm,
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

        plot.save_figure(self.output_path)
        self.save_waves_key(self.output_path, cmap, norm, threshold=wave_threshold)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Wave condition plotting sequence completed successfully.")

    def run(self):
        self.exit_if_disabled()
        # Get the GFS state for this updater
        self.get_gfs_state()
        self.grib_path = self.cache_path(f"gfs_waves_{self.forecast_hour_str}.grib2")

        url = f"{self.base_url}/gfs.{self.gfs_date_str}/{self.gfs_run}/wave/gridded/gfswave.t{self.gfs_run}z.global.0p25.f{self.forecast_hour_str}.grib2"
        if self.remote_data_update(remote_url=url, cache_file_path=self.grib_path):
            logger.info("Generating Waves plot...")
            self.plot()
