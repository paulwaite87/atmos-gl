#!/usr/bin/env python3
import os
import logging
import warnings
import numpy as np

# Internal imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.texture import encode_uv
from .common import Updater, MapData, _opaque_cmap, MultiHourRenderMixin, ForecastState
from atmos_gl.tiles import raster_tiles as rt

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Magnitude scale for the animated swell particle field (metres of significant wave
# height). The GPU layer clips |velocity| to this; pick a little above the tallest
# swell you care to distinguish. Must match VMAX_WAVES on the frontend (waves.js).
VMAX_WAVES = 8.0


# Wave-height gradients. The single source of truth now lives in the tile engine
# (raster_tiles.WAVES_PALETTES); re-exported here for the legend key renderer.
PALETTES = rt.WAVES_PALETTES



class WavesUpdater(Updater, MultiHourRenderMixin):
    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "Waves", map_data)

        # DESIGNED GRADIENTS FOR WAVE HEIGHT INTENSITY
        self.PALETTES = PALETTES

        # Per-hour velocity texture for the animated swell bars. The data_collector
        # stores a GFS-Wave u/v field per forecast hour in the fieldstore; render_all_hours
        # (in run()) writes waves_f{NNN}_data.png for each, alongside the heat tiles. The
        # "_data.png" entry tells the per-hour publish/staleness machinery what we emit.
        self.per_hour_outputs = ["_data.png"]
        self.status_product = "waves"

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
            _opaque_cmap(cmap),
            norm,
            [0, 2, 4, 6, 8],
            title,
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            weight="bold",
            decorate=_mark_threshold,
        )

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

    def plot_swell(self, field0, state: ForecastState):
        """Write the per-hour swell velocity texture (R=U east, G=V north) from a
        fieldstore field. The collector already derived u/v from swh + wave direction
        (see waves_data_unpack), so here we just encode the per-hour field — this is the
        animated-bars analogue of currents.plot, called once per catalog hour by
        render_all_hours. NaN/land cells in u/v become transparent (alpha 0) so bars
        respawn there. Separate from _write_velocity_texture (which encodes the single
        static base texture from the tile GRIB; kept for the forecast_stepping=off path)."""
        u = field0["u"]
        v = field0["v"]
        out_for_hour = self.get_output_path_for_hour(state.fhour)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", VMAX_WAVES)
        logger.info(
            f"Waves: wrote swell velocity texture f{state.fhour:03d}."
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
        # Done FIRST and unconditionally — the tile path below has early returns (no GRIB
        # yet / tile version unchanged) that must NOT skip the per-hour velocity render,
        # since those fields update independently of the heat-tile settings. Mirrors how
        # wind/currents render every catalog hour; gap-fills only missing/stale hours.
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker -- part 2 below
        # (tile baking) has no per-hour concept and isn't capped, but is cheap to re-check
        # every call (an early return once its version already matches).
        plotted = self.render_all_hours(
            "waves",
            plot_fn=self.plot_swell,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
            max_hours=max_hours,
        )

        # 2) Heat tiles + legend, baked from the fieldstore now-hour field (no GRIB).
        # The collector stores fhour_0..end, so the earliest catalog hour IS the now-hour;
        # both the heat tile and the static velocity texture bake from that one field.
        spec = rt.WAVES_SPEC

        resolved = self.latest_store_run(["waves"])
        if not resolved:
            logger.warning(
                "Waves: no waves field in the fieldstore yet (collector hasn't run); "
                "skipping tile build."
            )
            return plotted
        run_date, run_id, hours = resolved
        now_fh = hours[0]
        state = ForecastState.at_hour(run_date, run_id, now_fh)
        field0 = self.get_db_field_at_hour(state, "waves")
        if not field0 or field0.get("u") is None or field0.get("v") is None:
            logger.warning(
                "Waves: now-hour field missing u/v in the fieldstore; skipping tile build."
            )
            return plotted

        # The legend key is cheap to draw and depends on palette/alpha/threshold AND
        # key_fontsize. Refresh it whenever the task runs, so settings like key_fontsize
        # apply to the legend WITHOUT forcing a tile rebuild.
        self._write_legend_key()

        # Tiles depend only on the wave DATA identity (run/hour in the fieldstore) and the
        # settings that change tile pixels (palette, alpha, min_wave_height) — see
        # rt.current_version. Unrelated settings never change the version, so they never
        # trigger a (re)build.
        if rt.current_version(
            spec, self.config, state.run_date_str, state.run_id, now_fh
        ) == rt.published_version(spec, self.config):
            return plotted

        # Publish-then-fill: bake + publish the new version IMMEDIATELY so the API can
        # serve it on demand (the frontend's visible tiles render first — viewport
        # prioritised). Then warm the base pyramid in the background; the API keeps
        # serving on demand throughout, so the user never waits for the whole world.
        logger.info("Waves: data or tile settings changed — publishing dataset...")
        self._write_velocity_texture(field0)
        version, field, meta = rt.publish_dataset(
            spec, self.config, field0, state.run_date_str, state.run_id, now_fh
        )
        logger.info(f"Waves: version {version} published; warming base pyramid...")
        rt.warm_pyramid(spec, self.config, version, field, meta)
        logger.info("Waves: base pyramid warmed.")
        return plotted