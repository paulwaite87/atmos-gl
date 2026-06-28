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
from .common import Updater, MapData, Plot, _opaque_cmap, encode_uv

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Magnitude scale for the animated swell particle field (metres of significant wave
# height). The GPU layer clips |velocity| to this; pick a little above the tallest
# swell you care to distinguish. Must match VMAX_WAVES on the frontend (waves.js).
VMAX_WAVES = 8.0


# DESIGNED GRADIENTS FOR WAVE HEIGHT INTENSITY. Module-level so other renderers
# (e.g. the tile server) can reuse the exact same palettes as a single source of truth.
PALETTES = {
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


class WavesUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Waves", map_data)

        # DESIGNED GRADIENTS FOR WAVE HEIGHT INTENSITY
        self.PALETTES = PALETTES

        # Per-hour velocity texture for the animated swell bars. The data_collector
        # stores a GFS-Wave u/v field per forecast hour in the fieldstore; render_all_hours
        # (in run()) writes waves_f{NNN}_data.png for each, alongside the heat tiles. The
        # "_data.png" entry tells the per-hour publish/staleness machinery what we emit.
        self.per_hour_outputs = ["_data.png"]

    def save_waves_key(self, output_path, cmap, norm, threshold=0.0):
        """Generates a standalone Wave Height key image (separate _key.png)."""
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import matplotlib as mpl

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"
        key_fontsize = self.settings.get("key_fontsize", 10)

        fig = Figure(figsize=(4, 0.3))
        FigureCanvasAgg(fig)
        ax = fig.subplots()
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
        fig.clear()
        logger.debug(f"Saved Waves key to: {key_path}")

    def _coastline_mask(
        self, mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, res
    ):
        """Boolean land mask at grid resolution, cut from true coastline geometry.
        Thin wrapper over the shared common.coastline_land_mask (also used by currents)
        so the Natural Earth read/union is cached once across layers. Returns None on
        geometry-load failure so the caller falls back to the data-derived mask.
        """
        from .common import coastline_land_mask

        return coastline_land_mask(
            mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, res
        )

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

        alpha_setting = float(self.settings.get("alpha", 75) / 100)
        alpha_setting = np.clip(alpha_setting, 0.1, 1.0)

        # Level of detail controls BOTH the processing grid density and the coastline
        # resolution used to cut land out of the field. The wave data itself is 0.25 deg
        # so higher LOD doesn't invent wave detail, but it does sharpen the coastline
        # cutouts (the visibly "blocky" part) from a true coastline geometry.
        try:
            lod = int(self.settings.get("level_of_detail", 2))
        except (TypeError, ValueError):
            lod = 2
        lod = min(3, max(1, lod))
        # LOD controls the wave-field grid density (smoothness/detail). The coastline
        # mask always uses the most-detailed 10m geometry at high resolution (below),
        # independent of LOD, so land edges stay crisp at every level.
        grid_n = {1: 200, 2: 300, 3: 750}[lod]

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

        # --- LAND MASK FOR ARROWS ---
        # Cut land out of the arrow (u/v) field using true coastline geometry. The
        # displayed heat field is masked separately at high resolution below. If the
        # geometry can't be loaded (e.g. no network for Natural Earth), fall back to the
        # blocky-but-always-available nearest-neighbour mask from the wave data itself.
        COAST_RES = "10m"  # most-detailed Natural Earth coastline, for accurate edges
        grid_land_mask = self._coastline_mask(
            mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, COAST_RES
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

        u_grid[grid_land_mask] = np.nan
        v_grid[grid_land_mask] = np.nan

        # --- HIGH-RESOLUTION DISPLAY FIELD ---
        # The wave grid is coarse (0.25 deg data on a grid_n^2 mesh), so cutting land at
        # that resolution gives blocky coastlines. Instead, upsample the smooth field
        # onto a much finer mesh and cut land from the SAME coastline geometry at that
        # finer resolution, so coastline crispness is set by the mask grid (~HR_TARGET
        # px) and is independent of the wave-data resolution.
        from scipy.ndimage import zoom

        HR_TARGET = 2200  # target long-edge (px) for the mask/display grid
        upscale = max(1, int(np.ceil(HR_TARGET / grid_n)))
        hr_n = grid_n * upscale

        # Fill no-data cells before resampling so bilinear zoom doesn't smear NaNs, then
        # restore them at high res with a nearest-neighbour upsample (exact, no smearing).
        nan_mask = ~np.isfinite(swh_grid)
        fill_value = float(np.nanmean(swh_grid)) if not nan_mask.all() else 0.0
        swh_hr = zoom(np.where(nan_mask, fill_value, swh_grid), upscale, order=1)
        invalid_hr = zoom(nan_mask.astype(np.uint8), upscale, order=0).astype(bool)
        swh_hr[invalid_hr] = np.nan

        hr_lon = np.linspace(lon_min, lon_max, hr_n)
        hr_lat = np.linspace(lat_min, lat_max, hr_n)
        hr_mesh_lon, hr_mesh_lat = np.meshgrid(hr_lon, hr_lat)
        land_hr = self._coastline_mask(
            hr_mesh_lon, hr_mesh_lat, lon_min, lat_min, lon_max, lat_max, COAST_RES
        )
        if land_hr is None:
            # geometry unavailable: upsample the coarse mask we already computed
            land_hr = zoom(grid_land_mask.astype(np.uint8), upscale, order=0).astype(
                bool
            )
        swh_hr[land_hr] = np.nan

        # Apply the display threshold: below-threshold water becomes transparent on the
        # heat field, and the matching arrows are dropped too so they don't float over
        # bare base map. NaN comparisons are False, so land/no-data cells are unaffected.
        if wave_threshold > 0.0:
            with np.errstate(invalid="ignore"):
                swh_hr[swh_hr < wave_threshold] = np.nan
                arrow_below = swh_grid < wave_threshold
            u_grid[arrow_below] = np.nan
            v_grid[arrow_below] = np.nan

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
        # aspect="auto" is REQUIRED: imshow otherwise forces aspect="equal", which
        # overrides the axes' set_aspect("auto") and shrinks the square Web-Mercator
        # raster to half of a wide world figure. Heights above vmax clamp to the top
        # colour.
        plot.ax.imshow(
            swh_hr,
            extent=[hr_lon[0], hr_lon[-1], hr_lat[0], hr_lat[-1]],
            origin="lower",
            interpolation="bilinear",
            cmap=cmap,
            norm=norm,
            transform=ccrs.PlateCarree(),
            zorder=2,
            aspect="auto",
        )

        plot.save_figure(self.output_path)
        self.save_waves_key(self.output_path, cmap, norm, threshold=wave_threshold)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        logger.debug("Wave condition plotting sequence completed successfully.")

    def plot_swell(self, field0):
        """Write the per-hour swell velocity texture (R=U east, G=V north) from a
        fieldstore field. The collector already derived u/v from swh + wave direction
        (see waves_data_unpack), so here we just encode the per-hour field — this is the
        animated-bars analogue of currents.plot, called once per catalog hour by
        render_all_hours. NaN/land cells in u/v become transparent (alpha 0) so bars
        respawn there. Separate from _write_velocity_texture (which encodes the single
        static base texture from the tile GRIB; kept for the forecast_stepping=off path)."""
        u = field0["u"]
        v = field0["v"]
        out_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", VMAX_WAVES)
        logger.info(
            f"Waves: wrote swell velocity texture f{int(self.forecast_hour_str):03d}."
        )

    def _write_velocity_texture(self, field0):
        """Encode the now-hour swell vector field into <outfile_base>_data.png for the
        static (forecast_stepping=off) particle layer. u/v already come from the collector
        (direction = wave direction, magnitude = significant wave height, so taller swell
        drifts faster), already wrapped to a clean -180..180 equirect grid by the unpacker,
        so this just encodes the now-hour fieldstore field — the static-base analogue of
        plot_swell's per-hour textures. NaN/land cells stay transparent (alpha 0)."""
        base, _ = os.path.splitext(self.output_path)
        encode_uv(field0["u"], field0["v"], f"{base}_data.png", VMAX_WAVES)
        logger.info("Waves: wrote swell velocity texture for the animated layer.")

    def _write_legend_key(self):
        """Regenerate just the colourbar key (palette/threshold may have changed),
        without the slow world render — the heat field is served as tiles now."""
        import matplotlib.colors as mcolors

        palette_name = self.settings.get("palette", "ocean_storm")
        if palette_name not in self.PALETTES:
            palette_name = "ocean_storm"
        alpha_setting = float(
            np.clip(float(self.settings.get("alpha", 75) / 100), 0.1, 1.0)
        )
        try:
            threshold = max(0.0, float(self.settings.get("min_wave_height", 0) or 0))
        except (TypeError, ValueError):
            threshold = 0.0
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "wave_height",
            [(r, g, b, alpha_setting) for (r, g, b) in self.PALETTES[palette_name]],
            N=256,
        )
        norm = mcolors.Normalize(vmin=0.0, vmax=8.0)
        self.save_waves_key(self.output_path, cmap, norm, threshold=threshold)

    def run(self):
        # Get the GFS state for this updater
        self.get_gfs_state()

        # 1) Per-hour swell velocity textures for the animated bars, from the fieldstore.
        # Done FIRST and unconditionally — the tile path below has early returns (no GRIB
        # yet / tile version unchanged) that must NOT skip the per-hour velocity render,
        # since those fields update independently of the heat-tile settings. Mirrors how
        # wind/currents render every catalog hour; gap-fills only missing/stale hours.
        self.render_all_hours(
            "waves",
            plot_fn=self.plot_swell,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
        )

        # 2) Heat tiles + legend, baked from the fieldstore now-hour field (no GRIB).
        # The collector stores fhour_0..end, so the earliest catalog hour IS the now-hour;
        # both the heat tile and the static velocity texture bake from that one field.
        from worldmap.tiles import waves_tiles as wt

        resolved = self.latest_store_run(["waves"])
        if not resolved:
            logger.warning(
                "Waves: no waves field in the fieldstore yet (collector hasn't run); "
                "skipping tile build."
            )
            return
        self.run_date_str, self.run_id, hours = resolved
        now_fh = hours[0]
        field0 = self.get_db_field_at_hour("waves", now_fh)
        if not field0 or field0.get("u") is None or field0.get("v") is None:
            logger.warning(
                "Waves: now-hour field missing u/v in the fieldstore; skipping tile build."
            )
            return

        # The legend key is cheap to draw and depends on palette/alpha/threshold AND
        # key_fontsize. Refresh it whenever the task runs, so settings like key_fontsize
        # apply to the legend WITHOUT forcing a tile rebuild.
        self._write_legend_key()

        # Tiles depend only on the wave DATA identity (run/hour in the fieldstore) and the
        # settings that change tile pixels (palette, alpha, min_wave_height) — see
        # wt.current_version. Unrelated settings never change the version, so they never
        # trigger a (re)build.
        if wt.current_version(
            self.config, self.run_date_str, self.run_id, now_fh
        ) == wt.published_version(self.config):
            return

        # Publish-then-fill: bake + publish the new version IMMEDIATELY so the API can
        # serve it on demand (the frontend's visible tiles render first — viewport
        # prioritised). Then warm the base pyramid in the background; the API keeps
        # serving on demand throughout, so the user never waits for the whole world.
        logger.info("Waves: data or tile settings changed — publishing dataset...")
        self._write_velocity_texture(field0)
        version, field, meta = wt.publish_dataset(
            self.config, field0, self.run_date_str, self.run_id, now_fh
        )
        logger.info(f"Waves: version {version} published; warming base pyramid...")
        wt.warm_pyramid(self.config, version, field, meta)
        logger.info("Waves: base pyramid warmed.")