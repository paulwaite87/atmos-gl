#!/usr/bin/env python3
import os
import logging

import numpy as np
import matplotlib as mpl
import matplotlib.colors as mcolors

from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, encode_uv, _opaque_cmap

logger = logging.getLogger(__name__)


class CurrentsUpdater(Updater):
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

        # Speed-ramp palettes for the colourbar KEY + the fill layer's colour stops.
        # (The frontend fill reads these same stops; keep names in sync with config.)
        self.PALETTES = {
            "thermal_red": [(0.65, 0.0, 0.0), (1.0, 0.25, 0.0),
                            (1.0, 0.85, 0.0), (1.0, 1.0, 1.0)],
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
        import matplotlib.pyplot as plt

        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"
        key_fontsize = self.settings.get("key_fontsize", 10)

        cmap = mcolors.LinearSegmentedColormap.from_list(
            "current_speed", self.PALETTES[self._palette()], N=256
        )
        norm = mcolors.Normalize(vmin=0.0, vmax=self.VMAX_CURRENT)
        ticks = np.linspace(0.0, self.VMAX_CURRENT, 4)

        fig, ax = plt.subplots(figsize=(4, 0.3))
        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=_opaque_cmap(cmap)),
            cax=ax, orientation="horizontal", ticks=ticks,
        )
        cbar.ax.xaxis.set_major_formatter(plt.FormatStrFormatter("%.1f"))
        cbar.ax.set_title("Current Speed (m/s)", color="white",
                          fontsize=key_fontsize, pad=2, weight="bold")
        cbar.ax.tick_params(colors="white", labelsize=8)
        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        plt.close(fig)
        logger.debug(f"Saved Currents key to: {key_path}")

    def plot(self, field0):
        """Write the per-hour current velocity texture (R=U east, G=V north)."""
        u = field0["u"]  # m/s, regular grid (regridded by the collector)
        v = field0["v"]

        out_for_hour = self.get_output_path_for_hour(self.forecast_hour_str)
        base, _ = os.path.splitext(out_for_hour)
        encode_uv(u, v, f"{base}_data.png", self.VMAX_CURRENT)

        # Key is hour-independent; write once at the base name the frontend requests.
        self.save_currents_key(self.output_path)
        logger.info(
            f"Finished Currents velocity texture "
            f"f{int(self.forecast_hour_str):03d} (R=U, G=V)."
        )

    def run(self):
        # Resolve the RTOFS run (NOT GFS) and render every available hour's texture.
        self.get_rtofs_state()
        self.render_all_hours(
            "currents",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("u") is not None and f.get("v") is not None,
        )
