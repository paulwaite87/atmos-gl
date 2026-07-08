#!/usr/bin/env python3
import os
import logging

import numpy as np
import matplotlib.colors as mcolors

from worldmap.lib.config import WorldMapConfig
from worldmap.lib.texture import encode_uv
from .common import Updater, MapData, _opaque_cmap, coastline_land_mask, MultiHourRenderMixin, ForecastState

logger = logging.getLogger(__name__)


class CurrentsUpdater(Updater, MultiHourRenderMixin):
    """Ocean surface currents from RTOFS (via the fieldstore).

    Rebuilt for the GPU pipeline: the data_collector downloads RTOFS, regrids the
    tripolar u/v to a regular lat/lon grid, and stores it in the fieldstore. This
    task just reads that per-hour u/v field and writes ONE velocity texture per hour
    (R=U east, G=V north) — the same encoding wind uses. The frontend then renders
    BOTH a speed fill and advected particles from that single texture (speed is
    sqrt(u^2+v^2) computed in-shader, so no separate speed texture is needed).

    Reads use the RTOFS run (get_rtofs_state), NOT the GFS run.
    """

    VMAX_CURRENT = 2.5  # m/s; clips the strongest currents (Gulf Stream/Kuroshio ~2.5)

    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Currents", map_data)
        # No static PNG — only the GPU velocity texture (like wind).
        self.per_hour_outputs = ["_data.png"]
        self.status_product = "currents"
        # The land mask depends only on the (fixed) regrid geometry, so compute it once
        # per run and reuse for every hour. Keyed by grid shape.
        self._land_mask_cache = {}

        # Speed-ramp palettes for the colourbar KEY + the fill layer's colour stops.
        # (The frontend fill reads these same stops; keep names in sync with config.)
        self.PALETTES = {
            "thermal_red": [
                (0.65, 0.0, 0.0),
                (1.0, 0.25, 0.0),
                (1.0, 0.85, 0.0),
                (1.0, 1.0, 1.0),
            ],
            "electric_blue": [(0.0, 0.35, 0.55), (0.0, 0.85, 1.0), (0.75, 1.0, 1.0)],
            "toxic_neon": [(0.0, 0.45, 0.15), (0.25, 1.0, 0.0), (0.95, 1.0, 0.3)],
            "cyberpunk": [(0.45, 0.0, 0.45), (1.0, 0.0, 0.55), (0.0, 1.0, 0.75)],
        }

    def _palette(self):
        name = self.settings.get("palette", "thermal_red")
        return name if name in self.PALETTES else "thermal_red"

    def save_currents_key(self, output_path):
        """Standalone current-speed colourbar (_key.png). Regenerated each cycle so
        palette/range/font config changes take effect (no existence guard)."""
        cmap = mcolors.LinearSegmentedColormap.from_list(
            "current_speed", self.PALETTES[self._palette()], N=256
        )
        norm = mcolors.Normalize(vmin=0.0, vmax=self.VMAX_CURRENT)
        ticks = np.linspace(0.0, self.VMAX_CURRENT, 4)

        self.save_key_image(
            output_path,
            _opaque_cmap(cmap),
            norm,
            ticks,
            "Current Speed (m/s)",
            key_fontsize=self.settings.get("key_fontsize", 10),
            labelsize=8,
            tick_format="%.1f",
            weight="bold",
        )

    def _land_mask_for(self, lat, lon, shape):
        """Boolean land mask (True over land) on the current data grid, cut from true
        coastline geometry. Computed once per grid shape and cached for the run. Uses
        Natural Earth '50m' land — matched to the ~0.1 deg currents texture (finer than
        the texture can show, much cheaper than 10m over the whole globe). Returns None
        if geometry is unavailable, so plot() simply skips the cut that hour.
        """
        if shape in self._land_mask_cache:
            return self._land_mask_cache[shape]
        mesh_lon, mesh_lat = np.meshgrid(np.asarray(lon), np.asarray(lat))  # (nlat,nlon)
        land = coastline_land_mask(
            mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 90.0, res="50m"
        )
        self._land_mask_cache[shape] = land
        if land is not None:
            logger.info(
                f"Currents: built {shape} coastline land mask "
                f"({int(land.sum())} land cells cut)."
            )
        return land

    def plot(self, field0, state: ForecastState):
        """Write the per-hour current velocity texture (R=U east, G=V north).

        Before encoding we (1) drop water slower than current_speed_minimum (m/s) and
        (2) cut land out with true coastline geometry. Both become NaN -> alpha 0 in the
        texture, which hides them in BOTH layers at once: the fill shader discards
        alpha<0.5 texels, and the particle engine treats alpha<0.5 as land (no spawn /
        reset). So one data-side mask removes slow water and land encroachment from the
        speed fill and the flowing particles together.
        """
        u = np.asarray(field0["u"], dtype=np.float32).copy()
        v = np.asarray(field0["v"], dtype=np.float32).copy()

        # (1) Speed-minimum threshold (m/s). Below this -> no display. 0 disables.
        try:
            speed_min = float(self.settings.get("current_speed_minimum", 0.0))
        except (TypeError, ValueError):
            speed_min = 0.0
        if speed_min > 0.0:
            with np.errstate(invalid="ignore"):
                below = np.hypot(u, v) < speed_min   # NaN compares False -> left as-is
            u[below] = np.nan
            v[below] = np.nan

        # (2) Coastline cut: remove ocean values that the regrid smeared onto land.
        land = self._land_mask_for(field0.get("lat"), field0.get("lon"), u.shape)
        if land is not None and land.shape == u.shape:
            u[land] = np.nan
            v[land] = np.nan

        out_for_hour = self.get_output_path_for_hour(state.fhour)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", self.VMAX_CURRENT, lat=field0.get("lat"))

        # Key is hour-independent; write once at the base name the frontend requests.
        self.save_currents_key(self.output_path)
        logger.info(
            f"Finished Currents velocity texture "
            f"f{state.fhour:03d} (R=U, G=V)."
        )

    def run(self, max_hours=None):
        # Resolve the RTOFS run (NOT GFS). Warms the shared per-cycle baseline cache
        # (map_data.shared_state); render_all_hours resolves its own state from the
        # catalog below, so the return value here is unused.
        self.get_rtofs_state()
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            "currents",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
            max_hours=max_hours,
        )