#!/usr/bin/env python3
import os
import logging
import numpy as np
import cartopy.crs as ccrs
from scipy import ndimage

# Internal imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.texture import encode_frames
from .common import Updater, MapData, MultiHourRenderMixin, ForecastState
from .plotting import Plot

logger = logging.getLogger(__name__)

# Matches scalar_field.py's SPECS["temperature"] and temperature.js's VMIN/VMAX exactly
# -- this renders the SAME "temperature" fieldstore product the filled heatmap layer
# already consumes (no new GFS data needed), so it must decode the per-hour data
# texture with the identical encode_frames() convention.
VMIN_TEMPERATURE = -40.0
VMAX_TEMPERATURE = 50.0

# The Polar Boundary is fixed at the freezing isotherm -- not a configurable step like
# isobars' isobar_step, since 0 degC is the entire point of the layer.
FREEZE_LEVEL_C = 0.0

# Gaussian blur sigma (grid cells) applied before contouring -- knocks down single-cell
# noise spikes in the raw field (a "spikey" line) and softens land/sea temperature
# discontinuities enough that the live GPU shader's derivative-based line-width
# calculation (ui/modules/polar_boundary.js) doesn't get fooled by a sharp edge into
# drawing a false wide patch. Not user-configurable -- an internal data-cleanup step,
# not a stylistic choice the way palette/opacity are.
SMOOTHING_SIGMA = 1.5


def _smooth_global_field(values) -> np.ndarray:
    """Gaussian-blur a global (lat, lon) field, wrapping at the antimeridian (mode=
    "wrap" on the longitude axis) so column 0 and the last column blend correctly
    instead of the array edge reading as a hard boundary. The latitude axis uses
    "nearest" -- the poles aren't circularly continuous the way longitude is."""
    arr = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=0.0)
    return ndimage.gaussian_filter(arr, sigma=SMOOTHING_SIGMA, mode=("nearest", "wrap"))


def _merge_wrapped_labels(labels: np.ndarray, num_labels: int) -> np.ndarray:
    """ndimage.label has no concept of the longitude axis wrapping at the antimeridian
    -- two components that look separate only because the array's left/right edges are
    actually the same physical seam (column 0 sits right next to the last column on
    the globe) get merged here. A single shared ghost column (np.pad(..., mode="wrap"))
    is NOT enough: it only links each edge to a COPY of the opposite edge, so two
    components that each touch only one edge (e.g. one hugs column 0, the other hugs
    the last column, with nothing else connecting them) still end up with different
    labels. This instead unions the actual labels found at column 0 and the last
    column, per row -- small union-find, since at most a handful of components ever
    touch the seam."""
    parent = list(range(num_labels + 1))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for left, right in zip(labels[:, 0], labels[:, -1]):
        if left and right:
            union(int(left), int(right))

    merged = np.array([find(x) for x in range(num_labels + 1)])
    return merged[labels]


def _suppress_non_polar_cold_pockets(temperature: np.ndarray, lats) -> np.ndarray:
    """Push every sub-zero region NOT connected to a pole safely above freezing, so
    only the Polar Boundary itself (the edge of the large-scale polar cold airmass)
    draws a 0 degC contour -- not an isolated local cold pocket (e.g. NZ's Southern
    Alps in winter, or any other mountain range) that dips below freezing without
    being part of the actual polar front.

    "Connected" means reachable from the pole row through a continuous path of
    sub-zero cells (4-connectivity, wrapping at the antimeridian) -- deliberately NOT
    a fixed-latitude cutoff. The real Polar Boundary genuinely does sweep up and
    connect to NZ/Tasmania in winter at times, and correctly showing that is the whole
    point of this layer, not something to suppress.
    """
    cold = temperature < FREEZE_LEVEL_C
    if not cold.any():
        return temperature

    structure = ndimage.generate_binary_structure(2, 1)  # 4-connectivity
    labels, num = ndimage.label(cold, structure=structure)
    labels = _merge_wrapped_labels(labels, num)

    lats = np.asarray(lats)
    north_row = int(np.argmax(lats))
    south_row = int(np.argmin(lats))

    keep = np.zeros_like(cold)
    for row in (north_row, south_row):
        row_labels = set(np.unique(labels[row][cold[row]])) - {0}
        for lbl in row_labels:
            keep |= labels == lbl

    cleaned = temperature.copy()
    isolated = cold & ~keep
    cleaned[isolated] = VMAX_TEMPERATURE  # safely above freezing -> draws no contour
    return cleaned


class PolarBoundaryUpdater(Updater, MultiHourRenderMixin):
    """Renders the 0 degC isotherm ("Polar Boundary") as a single contour line -- the
    cold-front boundary line NZ weather broadcasts show creeping up from the Antarctic
    in winter. Consumes the SAME "temperature" fieldstore product scalar_field.py's
    filled heatmap already renders (status_product/run() pass "temperature", not a new
    product) -- just a different render of it: one fixed contour line instead of a
    filled colour ramp. Modeled on IsobarUpdater's contour-line pattern, simplified to a
    single level (no stepped range, no per-line labels -- a repeated "0" along one line
    adds nothing isobars' many distinct pressure values do).

    The raw temperature field is cleaned before contouring/encoding (see
    _smooth_global_field/_suppress_non_polar_cold_pockets above) so only the one true
    polar boundary per hemisphere renders, not every incidental 0 degC crossing (a cold
    mountain range, sensor noise) elsewhere on the globe."""

    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "polar_boundary", map_data)
        # Static PNG (matplotlib fallback) + GPU data texture (what the live shader,
        # ui/modules/polar_boundary.js, actually draws the line from).
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "temperature"

    def plot(self, field0, state: ForecastState):
        """Render the static fallback PNG (matplotlib contour, non-WebGL browsers only)
        AND the per-hour data texture. Mirrors IsobarUpdater.plot() minus the label
        harvesting -- see the class docstring for why labels aren't needed here."""
        logger.debug("Plotting Polar Boundary to per-hour output path")

        lats = field0["lat"]
        lons = field0["lon"]
        t = _smooth_global_field(field0["values"])
        t = _suppress_non_polar_cold_pockets(t, lats)

        plot = Plot(self.map_data.region)
        plot.get_figure()

        color = self.settings.get("line_color", "cyan")
        thickness = self.settings.get("linewidth", 2.0)
        alpha_val = float(self.settings.get("opacity", 90) / 100)

        plot.ax.contour(
            lons,
            lats,
            t,
            levels=[FREEZE_LEVEL_C],
            colors=color,
            linewidths=thickness,
            alpha=alpha_val,
            transform=ccrs.PlateCarree(),
            zorder=3,
        )

        # Per-hour output path
        output_path_for_hour = self.get_output_path_for_hour(state.fhour)
        plot.save_figure(output_path_for_hour)

        plt_close = getattr(plot, "close", None)
        if callable(plt_close):
            plt_close()

        # --- WebGL single-hour data texture (one frame per forecast hour; the
        # frontend scrubber assembles the animation from consecutive hours). Encodes
        # the SAME cleaned field the static contour above just drew, so the live GPU
        # line and the fallback PNG always agree. ---
        base, _ = os.path.splitext(output_path_for_hour)
        encode_frames([t], f"{base}_data.png", VMIN_TEMPERATURE, VMAX_TEMPERATURE)
        logger.info(f"Finished Polar Boundary texture f{state.fhour:03d}.")

    def run(self, max_hours=None):
        # Warms the shared per-cycle GFS baseline cache (map_data.shared_state) for
        # other updaters this cycle; render_all_hours resolves its own state from the
        # catalog below, so the return value here is unused.
        self.get_gfs_state()
        # Render EVERY available forecast hour (gap-filling), so the scrubber has a
        # PNG for each hour. should_plot_for_hour skips hours already fresh.
        # max_hours=1 from layer_builder's round-robin dispatch renders one hour and
        # returns, so this layer doesn't monopolise a render-pool worker.
        return self.render_all_hours(
            "temperature",
            plot_fn=self.plot,
            field_ready=lambda f: f.get("values") is not None,
            max_hours=max_hours,
        )
