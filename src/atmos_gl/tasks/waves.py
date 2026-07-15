#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np
from scipy.ndimage import distance_transform_edt

# Internal imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.texture import encode_uv
from atmos_gl.lib.coastline import coastline_land_mask
from .common import Updater, MapData, MultiHourRenderMixin, ForecastState
from .plotting import opaque_cmap
from atmos_gl.tiles import raster_tiles as rt

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Magnitude scale for the animated swell particle field (metres of significant wave
# height). The GPU layer clips |velocity| to this; pick a little above the tallest
# swell you care to distinguish. Must match VMAX_WAVES on the frontend (waves.js).
VMAX_WAVES = 8.0

# Fixed regrid step for waves' coastline-crispness pass, same reasoning and same
# empirically-timed value as SST's/currents' (see tasks/sst.py, tasks/currents.py):
# GFS-Wave's native 0.25 deg grid is coarser than this, and the true coastline mask
# needs a fine enough grid to snap to. Not a user setting, for the same reason.
_WAVES_REGRID_STEP_DEG = 0.08

# Wave-height gradients. Still sourced from the tile engine's registry (raster_tiles.py
# is left in place, un-registered from SPECS -- see docs/adr on the createFillLayer
# migration) purely to avoid duplicating this constant; re-exported here for the
# legend key renderer and the frontend's matching palette.
PALETTES = rt.WAVES_PALETTES



class WavesUpdater(Updater, MultiHourRenderMixin):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "Waves", map_data)

        # DESIGNED GRADIENTS FOR WAVE HEIGHT INTENSITY
        self.PALETTES = PALETTES

        # Per-hour velocity texture for the animated swell bars AND (via the frontend's
        # in-shader valueDecode) the heat fill -- both now read the SAME texture. The
        # data_collector stores a GFS-Wave u/v field per forecast hour in the fieldstore;
        # render_all_hours (in run()) writes waves_f{NNN}_data.png for each. The
        # "_data.png" entry tells the per-hour publish/staleness machinery what we emit.
        self.per_hour_outputs = ["_data.png"]
        self.status_product = "waves"
        # The land mask depends only on the (fixed) regrid geometry, so compute it once
        # per run and reuse for every hour. Keyed by grid shape. Mirrors currents.py.
        self._land_mask_cache = {}

    def save_waves_key(self, output_path, cmap, norm, threshold=0.0):
        """Generates a standalone Wave Height key image (separate _key.png)."""
        title = "Wave Height (m)"
        if threshold > 0.0:
            title = f"Wave Height (m) \u2265 {threshold:g}"

        def _mark_threshold(cbar):
            # Mark the transparent (below-threshold) zone on the key so the ramp's
            # visible start matches what's actually rendered on the map.
            if threshold > 0.0:
                cbar.ax.axvspan(norm.vmin, threshold, color="black", alpha=0.55)
                cbar.ax.axvline(threshold, color="white", linewidth=1.2)

        self.save_key_image(
            output_path,
            opaque_cmap(cmap),
            norm,
            [0, 2, 4, 6, 8],
            title,
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            weight="bold",
            decorate=_mark_threshold,
        )

    def _land_mask_for(self, lat, lon, shape):
        """Boolean land mask (True over land) on the regridded (_WAVES_REGRID_STEP_DEG)
        swell data grid, cut from true coastline geometry. Computed once per grid shape
        and cached for the run. Mirrors currents.py's _land_mask_for exactly. Returns
        None if geometry is unavailable, so callers simply skip the cut that hour.
        """
        if shape in self._land_mask_cache:
            return self._land_mask_cache[shape]
        mesh_lon, mesh_lat = np.meshgrid(np.asarray(lon), np.asarray(lat))
        land = coastline_land_mask(
            mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 90.0, res="50m"
        )
        self._land_mask_cache[shape] = land
        if land is not None:
            logger.info(
                f"Waves: built {shape} coastline land mask "
                f"({int(land.sum())} land cells cut)."
            )
        return land

    def _masked_uv(self, field0):
        """Regrid + true-coastline-mask u/v once, shared by BOTH the per-hour swell
        texture (particles) and, via the frontend's in-shader valueDecode, the heat
        fill -- one pass now serves what used to be two separate masking mechanisms
        (native-NaN-only for particles, a live per-tile-pixel STRtree cut for the
        heat tiles). Same technique SST/currents use: nearest-fill native NaN first
        (GFS-Wave's own no-data-over-land) so bilinear interpolation doesn't bleed
        outward from the coast into legitimate open water, regrid to
        _WAVES_REGRID_STEP_DEG, then cut the true coastline.

        Returns (new_lats, u, v) -- new_lats is passed straight through to encode_uv
        for correct north-at-top row orientation (see encode_uv's docstring).
        """
        u_native = np.asarray(field0["u"], dtype=np.float32).copy()
        v_native = np.asarray(field0["v"], dtype=np.float32).copy()
        lat_native = field0.get("lat")
        lon_native = field0.get("lon")

        for native in (u_native, v_native):
            bad = ~np.isfinite(native)
            if bad.any() and not bad.all():
                idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
                native[:] = native[tuple(idx)]

        new_lats, new_lons, u = self.regrid_for_lod(
            u_native, lat_native, lon_native, fill_value=np.nan,
            step_override=_WAVES_REGRID_STEP_DEG,
        )
        _, _, v = self.regrid_for_lod(
            v_native, lat_native, lon_native, fill_value=np.nan,
            step_override=_WAVES_REGRID_STEP_DEG,
        )

        land = self._land_mask_for(new_lats, new_lons, u.shape)
        if land is not None and land.shape == u.shape:
            u[land] = np.nan
            v[land] = np.nan

        return new_lats, u, v

    def plot_swell(self, field0, state: ForecastState):
        """Write the per-hour swell velocity texture (R=U east, G=V north) from a
        fieldstore field. The collector already derived u/v from swh + wave direction
        (see waves_data_unpack); _masked_uv regrids + cuts the true coastline before
        encoding -- this is the animated-bars analogue of currents.plot, called once
        per catalog hour by render_all_hours. Land/no-data cells in u/v become
        transparent (alpha 0) so bars respawn there and the heat fill (which decodes
        speed from this same texture client-side) shows nothing. Separate from
        _write_velocity_texture (writes the single static base texture; kept for the
        forecast_stepping=off path)."""
        new_lats, u, v = self._masked_uv(field0)
        out_for_hour = self.get_output_path_for_hour(state.fhour)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", VMAX_WAVES, lat=new_lats)
        logger.info(
            f"Waves: wrote swell velocity texture f{state.fhour:03d}."
        )

    def _write_velocity_texture(self, field0):
        """Encode the now-hour swell vector field into <outfile_base>_data.png for the
        static (forecast_stepping=off) particle layer. u/v already come from the collector
        (direction = wave direction, magnitude = significant wave height, so taller swell
        drifts faster), already wrapped to a clean -180..180 equirect grid by the unpacker;
        _masked_uv regrids + cuts the true coastline the same way plot_swell does --
        the static-base analogue of its per-hour textures. Land/no-data cells stay
        transparent (alpha 0)."""
        new_lats, u, v = self._masked_uv(field0)
        base, _ = os.path.splitext(self.output_path)
        encode_uv(u, v, f"{base}_data.png", VMAX_WAVES, lat=new_lats)
        logger.info("Waves: wrote swell velocity texture for the animated layer.")

    def _write_legend_key(self):
        """Regenerate just the colourbar key (palette/threshold may have changed),
        independent of the per-hour texture writes above."""
        import matplotlib.colors as mcolors

        palette_name = self.settings.get("palette", "ocean_storm")
        if palette_name not in self.PALETTES:
            palette_name = "ocean_storm"
        alpha_setting = float(
            np.clip(float(self.settings.get("opacity", 75) / 100), 0.1, 1.0)
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

    def run(self, max_hours=None):
        # Warms the shared per-cycle GFS baseline cache (map_data.shared_state) for
        # other updaters this cycle; both sections below resolve their own state from
        # the catalog, so the return value here is unused.
        self.get_gfs_state()

        # 1) Per-hour swell velocity textures for the animated bars, from the fieldstore.
        # Done FIRST and unconditionally, mirroring how wind/currents render every
        # catalog hour; gap-fills only missing/stale hours. max_hours=1 from
        # layer_builder's round-robin dispatch renders one hour and returns, so this
        # layer doesn't monopolise a render-pool worker.
        plotted = self.render_all_hours(
            "waves",
            plot_fn=self.plot_swell,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
            max_hours=max_hours,
        )

        # 2) Legend key + the static (forecast_stepping=off) base texture, from the
        # fieldstore now-hour field. The collector stores fhour_0..end, so the earliest
        # catalog hour IS the now-hour.
        resolved = self.latest_store_run(["waves"])
        if not resolved:
            logger.warning(
                "Waves: no waves field in the fieldstore yet (collector hasn't run); "
                "skipping static texture."
            )
            return plotted
        run_date, run_id, hours = resolved
        now_fh = hours[0]
        state = ForecastState.at_hour(run_date, run_id, now_fh)
        field0 = self.get_db_field_at_hour(state, "waves")
        if not field0 or field0.get("u") is None or field0.get("v") is None:
            logger.warning(
                "Waves: now-hour field missing u/v in the fieldstore; skipping static texture."
            )
            return plotted

        # The legend key is cheap to draw and depends on palette/alpha/threshold AND
        # key_fontsize. Refresh it whenever the task runs, so settings apply immediately.
        self._write_legend_key()
        # No version-gate needed now (that existed to skip the expensive tile pyramid
        # warm-up) -- _write_velocity_texture is just one more encode_uv call, cheap
        # enough to run unconditionally every cycle, same as the legend key above.
        self._write_velocity_texture(field0)
        return plotted