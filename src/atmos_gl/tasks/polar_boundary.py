#!/usr/bin/env python3
import os
import logging
import numpy as np
import cartopy.crs as ccrs
from scipy import ndimage, interpolate

# Internal imports
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.lib.coastline import coastline_land_mask
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

# Gaussian blur sigma (grid cells) applied before contouring. Beyond softening
# single-cell noise spikes and land/sea discontinuities (so the live GPU shader's
# derivative-based line-width calculation, ui/modules/polar_boundary.js, doesn't get
# fooled by a sharp edge into drawing a false wide patch), this is load-bearing for
# _isolate_antarctic_boundary's crossing sweep: wherever the real temperature-vs-
# latitude profile is nearly flat (hovering within a fraction of a degree of freezing
# across a wide latitude band -- common in the open Southern Ocean), small-scale grid
# noise can flip which row counts as the first 0 degC crossing by several degrees of
# latitude between one 0.25 deg column and the next, even though nothing resembling a
# front boundary actually moved -- confirmed against live data, where sigma=1.5
# produced crossing jumps up to 5 degrees between adjacent columns, rendering as sharp
# spikes and closed loops that BOUNDARY_SMOOTHING_DEG's longitude-only smoothing
# couldn't fully absorb afterward. 10 collapses those jumps to <=0.5 degrees at the
# source, before the sweep ever runs, without erasing genuine regional structure (a
# front bulging out over one ocean basin, retreating over another) -- see
# tests/test_polar_boundary.py's crossing-stability test for the reproduction. Not
# user-configurable -- an internal data-cleanup step, not a stylistic choice the way
# palette/opacity are.
SMOOTHING_SIGMA = 10.0

# Degrees of longitude for the median filter _isolate_antarctic_boundary runs over
# the raw crossing-latitude curve BEFORE the smoothing spline -- the primary defence
# against the 2D nearest-ocean land fill's own artifact: a wide inland region (deep
# Patagonia, not just coastal Chile) can have many longitude columns share the
# identical nearest true-ocean cell, so the filled "sea-level" value is a flat
# plateau with a hard edge wherever that nearest cell changes. Confirmed against live
# data, two adjacent plateaus differing by under 3 degC (both near freezing, where
# the latitude gradient is shallow) produced a 15 degree crossing-latitude jump.
#
# A median filter was chosen over just cranking the spline's smoothing factor because
# it's a LOCAL operation: it erases a short run of outlier values (the plateau edge,
# a handful of columns wide) without forcing a single global smoothness budget across
# the entire curve the way FITPACK's `s` does -- an `s` large enough to absorb this
# one artifact (see BOUNDARY_SMOOTHING_DEG's old value, 3.0) ended up flattening
# genuine regional structure (a front bulging out over one ocean basin, retreating
# over another) everywhere else too, down to a nearly perfect circle. 5 degrees
# (~21 grid cells at 0.25 deg resolution) fully absorbs the Patagonia case with
# margin (window=15 already sufficed) while leaving synoptic-scale bulges, which
# span tens of degrees, untouched -- see tests/test_polar_boundary.py's plateau-fill
# regression test.
MEDIAN_FILTER_DEG = 5.0

# Estimated per-column noise (degrees latitude) remaining in the crossing-latitude
# curve AFTER the median filter above, used to set the periodic smoothing spline's
# smoothing factor in _isolate_antarctic_boundary (FITPACK's s ~= n_columns * std**2
# for i.i.d. noise of this std -- see scipy.interpolate.splrep). Only a light cosmetic
# pass now that the median filter handles real artifacts: turns the median filter's
# blocky, piecewise-constant output into a continuous curve, and -- critically --
# replaces the discrete grid-row cutoff the old implementation used with a genuinely
# continuous curve; see _isolate_antarctic_boundary's docstring for why the discrete
# cutoff stayed visibly stepped at high zoom even after smoothing it. Deliberately
# small: this is no longer responsible for absorbing large artifacts, so it doesn't
# need to be, and shouldn't be -- see MEDIAN_FILTER_DEG's docstring for what happened
# when this constant alone was pushed up to do that job.
BOUNDARY_SMOOTHING_DEG = 0.6

# Bounding boxes (lon_min, lat_min, lon_max, lat_max) for the "except when over NZ or
# Tasmania" exception in _isolate_antarctic_boundary -- generous enough to cover both
# landmasses (plus a little surrounding sea) without reaching into mainland Australia
# or the wider Pacific.
NZ_BBOX = (165.5, -47.5, 179.0, -34.0)
TASMANIA_BBOX = (143.5, -43.8, 148.7, -39.5)


def _smooth_global_field(values) -> np.ndarray:
    """Gaussian-blur a global (lat, lon) field, wrapping at the antimeridian (mode=
    "wrap" on the longitude axis) so column 0 and the last column blend correctly
    instead of the array edge reading as a hard boundary. The latitude axis uses
    "nearest" -- the poles aren't circularly continuous the way longitude is."""
    arr = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=0.0)
    return ndimage.gaussian_filter(arr, sigma=SMOOTHING_SIGMA, mode=("nearest", "wrap"))


def _in_bbox(lon_grid: np.ndarray, lat_grid: np.ndarray, bbox) -> np.ndarray:
    lon_min, lat_min, lon_max, lat_max = bbox
    return (
        (lon_grid >= lon_min)
        & (lon_grid <= lon_max)
        & (lat_grid >= lat_min)
        & (lat_grid <= lat_max)
    )


def _isolate_antarctic_boundary(
    temperature: np.ndarray,
    lats,
    lons,
    land_mask: np.ndarray,
    smoothing_deg: float = BOUNDARY_SMOOTHING_DEG,
) -> np.ndarray:
    """Reduce the field to a single Antarctic Polar Boundary: for each longitude
    column, sweep from the South Pole northward to find the FIRST 0 degC crossing,
    median-filter those crossing latitudes across longitude to erase localised
    artifacts (see MEDIAN_FILTER_DEG), fit a light smoothing periodic cubic spline
    over the result purely for cosmetic continuity (see BOUNDARY_SMOOTHING_DEG), then
    rebuild the ENTIRE field as a signed distance (in degrees latitude) from that
    curve -- negative south of it, positive north, exactly 0 on it. Every cell's
    value now depends only on its own latitude and its column's curve latitude, not
    on the real temperature there, so only one smooth line, per column, ever crosses
    freezing, and the Northern Hemisphere (strongly positive by construction,
    backstopped explicitly below) never does.

    Rebuilding via signed distance -- not a discrete "north of this grid row ->
    VMAX_TEMPERATURE" cutoff -- is what makes the line smooth at any zoom level.
    Discrete cutoffs stayed pinned to whichever integer grid row is nearest even
    after smoothing the (fractional) boundary heavily, because comparing a row INDEX
    to a smoothed float threshold still snaps the actual field transition to a grid
    row -- adjacent columns' transitions differ by a whole row (0.25 deg) far more
    often than the smoothed curve itself does, which reads as a visible staircase
    once zoomed in past a few grid cells, even though the broad shape looks smooth
    zoomed out. A signed-distance field has no such snap: matplotlib's contour()
    interpolates it continuously between grid points, and the frontend's bicubic
    texture sampling (ui/modules/polar_boundary.js) does the same in the GPU shader,
    so both renderers trace the actual spline curve rather than its grid-quantised
    shadow.

    The sweep reads sea-level (ocean) temperature, not land's -- high-altitude terrain
    (the Andes, the Southern Alps) is permanently cold regardless of season for
    reasons that have nothing to do with the polar airmass, and there's no
    elevation/terrain dataset in this pipeline to correct for that directly. Instead,
    land cells are excluded from the sweep and take on the temperature of the nearest
    TRUE ocean cell in any direction (2D nearest-neighbour fill, the same
    distance_transform_edt technique currents.py/waves.py use for their own native-NaN
    land fill) -- not the nearest ocean reading further south along the same meridian.
    A straight south-to-north forward-fill breaks down for a long, narrow, mostly-
    coastal country (Chile): at a fixed longitude that stays on land from Patagonia
    almost to Santiago, it would drag one lone reading from wherever that meridian
    last touched water (often far south, near the Strait of Magellan) up the entire
    length of the country, inventing a tall, narrow false spike inland. The nearest
    true ocean cell is normally just offshore to the west instead. NZ and Tasmania are
    the deliberate exception: the real Polar Boundary reaching them in winter is the
    whole point of the layer (the front NZ weather broadcasts show creeping up from
    the Antarctic), so their own -- possibly terrain-cold -- readings are used
    directly rather than papered over by the sea-level fill.

    smoothing_deg controls the spline's smoothing factor (see BOUNDARY_SMOOTHING_DEG's
    docstring) and doubles as the on/off switch: 0 skips fitting entirely and uses the
    raw per-column crossing latitude (tests exercise the crossing/land/NZ logic in
    isolation this way, without the spline as a confound).
    """
    lats = np.asarray(lats)
    lons = np.asarray(lons)
    n_rows = temperature.shape[0]
    n_cols = temperature.shape[1]

    order = np.argsort(lats)  # row indices, South Pole -> North Pole
    ordered_lats = lats[order]

    lon_grid, lat_grid = np.meshgrid(lons, lats)
    exception = _in_bbox(lon_grid, lat_grid, NZ_BBOX) | _in_bbox(
        lon_grid, lat_grid, TASMANIA_BBOX
    )
    use_actual = exception | ~land_mask
    sea_level = np.where(use_actual, temperature, np.nan).astype(np.float64)
    missing = np.isnan(sea_level)
    if missing.any() and not missing.all():
        nearest = ndimage.distance_transform_edt(
            missing, return_distances=False, return_indices=True
        )
        sea_level = sea_level[tuple(nearest)]

    ordered = sea_level[order]

    is_warm = ordered >= FREEZE_LEVEL_C
    crosses = is_warm.any(axis=0)
    first_warm_row = np.where(crosses, is_warm.argmax(axis=0), n_rows - 1)

    # Sub-grid-precision crossing latitude: linearly interpolate between the last
    # cold row and the first warm row (the same interpolation matplotlib's own
    # marching-squares contour() does internally) instead of snapping to whichever
    # grid row happened to cross first -- this is the fractional input the spline
    # below needs to actually smooth BETWEEN grid points, not just across them.
    prev_row = np.clip(first_warm_row - 1, 0, n_rows - 1)
    cols = np.arange(n_cols)
    v0 = ordered[prev_row, cols]
    v1 = ordered[first_warm_row, cols]
    lat0 = ordered_lats[prev_row]
    lat1 = ordered_lats[first_warm_row]
    span = v1 - v0
    safe_span = np.where(np.abs(span) > 1e-9, span, 1.0)
    frac = np.where(np.abs(span) > 1e-9, (FREEZE_LEVEL_C - v0) / safe_span, 0.0)
    frac = np.clip(frac, 0.0, 1.0)
    boundary_lat = np.where(crosses, lat0 + frac * (lat1 - lat0), ordered_lats[-1])

    if smoothing_deg > 0 and n_cols > 3:
        sort_idx = np.argsort(lons)
        x = lons[sort_idx]
        y = boundary_lat[sort_idx]

        # Median filter FIRST -- see MEDIAN_FILTER_DEG's docstring for why this (a
        # local operation) carries the load against real artifacts like the land
        # fill's plateau edges, rather than the spline below.
        lon_step = abs(float(lons[1] - lons[0])) if n_cols > 1 else 1.0
        if lon_step > 0:
            window = max(3, int(round(MEDIAN_FILTER_DEG / lon_step)) | 1)  # odd
            y = ndimage.median_filter(y, size=window, mode="wrap")

        try:
            # FITPACK smoothing factor: s ~= n_cols * std**2 for i.i.d. per-point
            # noise of standard deviation smoothing_deg (degrees latitude).
            tck = interpolate.splrep(x, y, per=1, s=(smoothing_deg**2) * n_cols)
            boundary_lat = np.empty(n_cols)
            boundary_lat[sort_idx] = interpolate.splev(x, tck)
        except Exception as e:
            logger.warning(f"Polar Boundary: spline fit failed ({e}); using raw crossing.")

    # Signed distance (degrees latitude) from the smooth boundary curve, clipped to
    # the same range encode_frames uses -- negative (cold/kept) south of it, positive
    # (warm/suppressed) north, exactly 0 on the curve itself.
    signed = lats[:, None] - boundary_lat[None, :]
    cleaned = np.clip(signed, VMIN_TEMPERATURE, VMAX_TEMPERATURE)
    # Never the Arctic -- this layer draws exactly one (Antarctic) boundary.
    cleaned[lats > 0.0, :] = VMAX_TEMPERATURE
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
    _smooth_global_field/_isolate_antarctic_boundary above) so only the single
    Antarctic Polar Boundary renders -- never the Arctic, and never an incidental 0
    degC crossing (a cold mountain range, sensor noise) elsewhere on the globe."""

    def __init__(self, config: AtmosGLConfig, map_data: MapData):
        super().__init__(config, "polar_boundary", map_data)
        # Static PNG (matplotlib fallback) + GPU data texture (what the live shader,
        # ui/modules/polar_boundary.js, actually draws the line from).
        self.per_hour_outputs = [".png", "_data.png"]
        self.status_product = "temperature"
        # The land mask depends only on the (fixed) temperature grid geometry, so
        # compute it once and reuse for every hour. Mirrors currents.py/waves.py's
        # _land_mask_for caching.
        self._land_mask_cache = {}

    def _land_mask_for(self, lats, lons, shape):
        """Boolean land mask (True over land), Southern Hemisphere only -- this layer
        never draws north of the equator (see _isolate_antarctic_boundary). Cut from
        true coastline geometry; computed once per grid shape and cached for the run.
        Returns None if geometry is unavailable, so plot() falls back to treating
        everything as sea level.
        """
        if shape in self._land_mask_cache:
            return self._land_mask_cache[shape]
        mesh_lon, mesh_lat = np.meshgrid(np.asarray(lons), np.asarray(lats))
        land = coastline_land_mask(
            mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 0.0, res="50m"
        )
        self._land_mask_cache[shape] = land
        if land is not None:
            logger.info(
                f"Polar Boundary: built {shape} coastline land mask "
                f"({int(land.sum())} land cells excluded from the sea-level sweep)."
            )
        return land

    def plot(self, field0, state: ForecastState):
        """Render the static fallback PNG (matplotlib contour, non-WebGL browsers only)
        AND the per-hour data texture. Mirrors IsobarUpdater.plot() minus the label
        harvesting -- see the class docstring for why labels aren't needed here."""
        logger.debug("Plotting Polar Boundary to per-hour output path")

        lats = field0["lat"]
        lons = field0["lon"]
        t = _smooth_global_field(field0["values"])
        land = self._land_mask_for(lats, lons, t.shape)
        if land is None:
            land = np.zeros(t.shape, dtype=bool)
        t = _isolate_antarctic_boundary(t, lats, lons, land)

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
