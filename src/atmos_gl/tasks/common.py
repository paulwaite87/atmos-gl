#!/usr/bin/env python3
import os
import sys
import json
import logging
import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
import cartopy.crs as ccrs
import cartopy.mpl.geoaxes as geoaxes
import numpy as np
from scipy.interpolate import RegularGridInterpolator
from typing import cast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# Internal library import
from atmos_gl.lib.config import AtmosGLConfig
from atmos_gl.db.region_adapter import RegionAdapter
from atmos_gl.lib import fieldstore
from atmos_gl.db.process_status_adapter import ProcessStatusAdapter
from atmos_gl.lib.data_status import (
    freshness_percent,
    estimate_next_update,
    period_s_from_runs_per_day,
    read_process_status,
    build_status,
)

logger = logging.getLogger(__name__)

WEB_MERCATOR = ccrs.Mercator.GOOGLE  # EPSG:3857
MERCATOR_LAT_LIMIT = 85.0511  # NOTE: just *inside* GOOGLE's 85.0511288 max

# Seconds between layer_builder's fan-out cycles (every cycle dispatches every updater).
# Canonical home is here, not layer_builder.py, so Updater.layer_status() can use it for
# next_update without layer_builder importing tasks.common creating a cycle the other way.
# layer_builder.py imports this rather than defining its own copy.
LAYER_CYCLE_SECONDS = 15

# Upper bound on points in a regrid_for_lod() output grid (~32MB per float64 array at
# the cap). regrid_for_lod's LOD step sizes are tuned to stay comfortably under this at
# world-view scale (the dominant case: the frontend always projects onto a globe), but
# an earlier tuning (0.05/0.125/0.25 degrees, sized for a regional view) applied
# unscaled to a world-view bbox (360x180 degrees) ballooned "high" to ~26M points per
# array and reliably OOM-killed the render worker under concurrent load. This budget is
# a backstop for that failure mode, not the primary mechanism — regrid_for_lod scales
# its step up (coarser) only if the clipped region is large enough to exceed it anyway.
_MAX_LOD_GRID_POINTS = 4_000_000


# Cache the unioned Natural Earth land geometry per (resolution, rounded bbox) so we
# read the shapefile and union it once, then reuse across hours and across layers
# (waves + currents share this). Module-level so it survives per-hour Updater instances.
_COAST_GEOM_CACHE = {}


def coastline_land_mask(mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, res="10m"):
    """Boolean land mask (True over land) sampled at the given mesh, cut from true
    Natural Earth coastline geometry at resolution `res` ('10m' / '50m' / '110m').

    Shared by any layer that needs to remove land from an ocean field (waves, currents):
    a data-derived NaN mask only knows where the model lacked data, so model values can
    smear up to the interpolation cap onto the coast; cutting against real coastline
    polygons clips the field to the actual shoreline. Returns None if the geometry can't
    be loaded (e.g. no network for the Natural Earth download) so callers can fall back
    to whatever data-derived mask they have.

    Pick `res` to match the target grid: 10m for fine regional grids, 50m for coarser
    global grids (cheaper, and finer than the texture can show anyway).
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
        land_geom = _COAST_GEOM_CACHE.get(key)
        if land_geom is None:
            land = cfeature.NaturalEarthFeature("physical", "land", res)
            geoms = list(
                land.intersecting_geometries([lon_min, lon_max, lat_min, lat_max])
            )
            if not geoms:
                # No land in this region -> everything is water.
                return np.zeros(np.shape(mesh_lon), dtype=bool)
            land_geom = unary_union(geoms)
            _COAST_GEOM_CACHE[key] = land_geom

        try:
            import shapely

            mask = shapely.contains_xy(land_geom, mesh_lon, mesh_lat)
        except (ImportError, AttributeError):
            import shapely.vectorized as shpvec

            mask = shpvec.contains(land_geom, mesh_lon, mesh_lat)
        return np.asarray(mask, dtype=bool)
    except Exception as exc:  # network/data/parse failure -> graceful fallback
        logger.warning(
            f"Coastline geometry unavailable ({exc!r}); land mask skipped."
        )
        return None


def _opaque_cmap(cmap, n=256):
    """Return an opaque copy of a colormap (alpha forced to 1.0)."""
    import numpy as np
    import matplotlib.colors as mcolors

    colors = cmap(np.linspace(0, 1, n))  # (n, 4) RGBA
    colors[:, 3] = 1.0
    return mcolors.ListedColormap(colors)


def stringify_bbox(bbox):
    """
    Converts a bbox list into a filename-safe string.
    Example: [-180.0, -90.0, 180.0, 90.0] -> "180.0W_90.0S_180.0E_90.0N"
    Or simpler: "lon-180.0_lat-90.0_lon180.0_lat90.0"
    """
    if not bbox or len(bbox) != 4:
        return "global"

    labels = ["w", "s", "e", "n"]
    return "_".join(f"{labels[i]}{abs(bbox[i]):.1f}" for i in range(4))


def get_bbox_center(bbox):
    """
    Returns the center (longitude, latitude) for a given bbox.
    bbox: [lon_min, lat_min, lon_max, lat_max]
    """
    lon_min, lat_min, lon_max, lat_max = bbox

    # Center Latitude is a straight average
    center_lat = (lat_min + lat_max) / 2

    # Center Longitude
    # Handle the Date Line: if the span is negative or crosses 180
    delta_lon = lon_max - lon_min
    center_lon = lon_min + (delta_lon / 2)

    # Normalize longitude to stay within [-180, 180]
    if center_lon > 180:
        center_lon -= 360
    elif center_lon < -180:
        center_lon += 360

    return center_lon, center_lat


class MapRegion:
    def __init__(
        self,
        target_geometry: str | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
        region: str | list[float] | None = None,
    ):
        self.region = region
        self.region_identifier = "region"
        self.target_width = target_width
        self.target_height = target_height
        self.region_geometry = target_geometry
        # Solve inter-dependency of these dimensions; explicit dims get
        # priority over the composite geometry string
        if isinstance(target_width, int) and isinstance(target_height, int):
            self.target_geometry = f"{self.target_width}x{self.target_height}"
        elif target_geometry and "x" in target_geometry:
            self.target_width = int(target_geometry.split("x")[0])
            self.target_height = int(target_geometry.split("x")[1])
        self.bbox = None
        self.world_view = False
        self.centre_latitude = 0.0
        self.centre_longitude = 0.0
        self.set_map_region_data(region)

    def is_in_region(self, lat: float, lon: float):
        return (
            self.bbox[1] <= lat <= self.bbox[3] and self.bbox[0] <= lon <= self.bbox[2]
        )

    def set_map_region_data(self, region: str | list[float] | None):
        bbox = None
        bbox_prefix = "region_"
        self.world_view = False

        # Handle explicit 'falsy' regions (None, empty string)
        if not region:
            bbox = [-180.0, -90.0, 180.0, 90.0]
            self.world_view = True
            bbox_prefix = "bbox_"

        elif str(region).startswith("["):
            try:
                data = json.loads(str(region))
                if isinstance(data, list) and not data:
                    bbox = [-180.0, -90.0, 180.0, 90.0]
                    self.world_view = True
                    bbox_prefix = "global_"
                else:
                    bbox = [float(x) for x in data]
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                logger.error(f"Invalid BBox format for '{region}': {e}")

        else:
            # Database lookup
            region_adapter = RegionAdapter()
            bbox_row = region_adapter.get_region_definition(str(region))
            if bbox_row:
                bbox = [val for _, val in bbox_row.items()]
                bbox_prefix = f"{bbox_prefix}_{region}"
            else:
                logger.warning(
                    f"Region label '{region}' not found; defaulting to global"
                )
                bbox = [-180.0, -90.0, 180.0, 90.0]
                self.world_view = True
                bbox_prefix = "global_"

        # Apply aspect ratio adjustment and 180-degree safety shift
        if bbox:
            self.bbox = bbox
            self.region_identifier = f"{bbox_prefix}_{stringify_bbox(bbox)}"
            self.centre_longitude, self.centre_latitude = get_bbox_center(bbox)


class MapData:
    def __init__(self, config: AtmosGLConfig):
        self.config = config
        self.region = None
        self.shared_state = {}
        self.refresh()

    def refresh(self):
        # Acquire the target geometry
        common_settings = self.config.get_section("common")
        target_geometry = common_settings.get("target_geometry", "2048x1024")
        self.region = MapRegion(target_geometry=target_geometry)


class Plot:
    def __init__(self, region: MapRegion, projection=WEB_MERCATOR):
        self.region = region
        self.projection = projection
        self.fig = None
        self.ax = None

    def get_figure(self):
        plot_target_width = float(self.region.target_width) / 100
        plot_target_height = float(self.region.target_height) / 100
        # OO figure with an explicit Agg canvas — no pyplot, no global state, thread-safe.
        self.fig = Figure(figsize=(plot_target_width, plot_target_height), dpi=100)
        FigureCanvasAgg(self.fig)
        self.ax = cast(
            geoaxes.GeoAxes, self.fig.add_axes((0, 0, 1, 1), projection=self.projection)
        )
        bbox = self.region.bbox
        lat_lo = max(bbox[1], -MERCATOR_LAT_LIMIT)
        lat_hi = min(bbox[3], MERCATOR_LAT_LIMIT)
        # extent is ALWAYS given in lon/lat degrees, regardless of axes projection
        self.ax.set_extent([bbox[0], bbox[2], lat_lo, lat_hi], crs=ccrs.PlateCarree())
        self.ax.set_aspect("auto", adjustable="box")

    def save_figure(self, output_path: str):
        self.ax.set_axis_off()
        self.ax.patch.set_alpha(0)
        self.fig.patch.set_alpha(0)

        # Atomic write/move to avoid timing issues
        base, ext = os.path.splitext(output_path)
        tmp_img = f"{base}.tmp{ext}"
        self.fig.savefig(tmp_img, transparent=True, bbox_inches=None, pad_inches=0)
        os.replace(tmp_img, output_path)

        # No global figure registry to close; just release the artists.
        self.fig.clear()


@dataclass(frozen=True)
class ForecastState:
    """Which forecast run + hour a render call operates on (GFS or RTOFS; the same
    shape either way). Passed explicitly wherever a render needs to know "when" -- see
    CONTEXT.md's "ForecastState" entry. Two ways to build one:
      * Updater.get_gfs_state()/get_rtofs_state() -- the shared per-cycle NOMADS/RTOFS
        baseline plus a computed now-offset.
      * ForecastState.at_hour(run_date, run_id, fhour) -- a specific catalog hour,
        used when iterating every hour a run has data for (render_all_hours,
        layer_status, and the few callers that resolve their own catalog run).
    """

    run_date_str: str
    run_id: str
    forecast_hour_str: str

    @property
    def fhour(self) -> int:
        return int(self.forecast_hour_str)

    @classmethod
    def at_hour(cls, run_date_str, run_id, fhour) -> "ForecastState":
        return cls(run_date_str, run_id, f"{int(fhour):03d}")


class Updater:
    def __init__(self, config: AtmosGLConfig, section: str, map_data: MapData):
        self.config = config
        self.map_data = map_data
        self.section = section.lower()
        self.settings = config.get_section(self.section)
        self.common = config.get_section("common")
        self.animation = config.get_section("animation")
        self.workdir = self.common.get("workdir", ".")
        # Own, independent store+connection per updater (NOT the shared singleton), so the
        # async fan-out in layer_builder can run updaters concurrently without sharing a
        # psycopg2 connection across threads.
        self._store = fieldstore.make_store(self.workdir)
        self.process_status_adapter = ProcessStatusAdapter()
        self.outfile = self.settings.get("outfile", "")
        self.output_path = None
        self.enabled = self.settings.get("enabled", False)
        # This is the starting hour (offset) for all renders. It used to be a
        # configurable setting, but since we moved to creating all renders for
        # each hour, and allow the user to play through them, this is not
        # useful to them. We hard-code it to zero here for now.
        self.forecast_hour = 0
        # Per-hour output suffixes a COMPLETE render produces for this layer, relative
        # to the per-hour base (e.g. "isobars_f004"). should_plot_for_hour treats an
        # hour as stale if ANY of these is missing, so deleting (say) a _data.png
        # forces a re-render even when the static .png still exists. Subclasses
        # override this to match what their plot() actually writes. Default: a single
        # static PNG (legacy/plain layers).
        self.per_hour_outputs = [".png"]

        # Fieldstore product this task renders from, for layer_status()'s multi-hour %
        # (see render_all_hours). None (default) means single-shot: sst/clouds/markers
        # don't render per-forecast-hour, so layer_status() falls back to a decaying
        # freshness gauge instead. Multi-hour subclasses (isobars, wind, ...) set this.
        self.status_product: str | None = None

        # Copy map data up to this class for convenience
        self.target_width = map_data.region.target_width
        self.target_height = map_data.region.target_height
        self.world_view = map_data.region.world_view
        self.map_region_identifier = map_data.region.region_identifier
        self.centre_longitude = map_data.region.centre_longitude
        self.centre_latitude = map_data.region.centre_latitude
        self.map_region_bbox = map_data.region.bbox

        # Always set these, which can be over-ridden later if required.
        # If the updater doesn't have an outfile defined, this does nothing.
        self.set_output_path()
        self.base_url = self.get_base_url()

    def get_output_path(self) -> str | None:
        return (
            str(os.path.join(self.common.get("workdir", "."), self.outfile))
            if self.outfile
            else None
        )

    def set_output_path(self):
        self.output_path = self.get_output_path()
        if self.output_path:
            file_path = Path(self.output_path)
            # Safely verify directories exist for non-image files
            if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
                os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
                # Use append mode ('a') to touch/create the file if missing
                with open(self.output_path, "a") as _:
                    pass

    def get_output_path_if_exists(self, section=None):
        """Returns an output path for the given section, but only if the file exists"""
        outfile = self.config.get_setting(
            section if section else self.section, "outfile"
        )
        if outfile:
            output_path = str(os.path.join(self.common.get("workdir", "."), outfile))
            if os.path.exists(output_path):
                return output_path
        return None

    def cache_path(self, filename: str) -> str:
        """Path for a downloadable cache file under the data dir.

        Every cache file carries a uniform '<section>_cache_' prefix. This lets the
        housekeeper find and expire caches by a single marker (no per-layer pattern
        lists to maintain), and makes live render outputs - which never carry the
        prefix - safe from deletion by construction rather than by a guard list.
        """
        return str(
            os.path.join(self.workdir, "data", f"{self.section}_cache_{filename}")
        )

    def get_base_url(self):
        return self.settings.get("url", "").rstrip("/")

    def remove_output_file(self):
        """Clears the output file of this updater if it exists"""
        output_path = self.get_output_path()
        if output_path and os.path.exists(output_path) and os.path.isfile(output_path):
            os.remove(output_path)

    def exit_if_disabled(self):
        if not self.enabled:
            logger.info(f"{self.section} task disabled; skipping")
            output_path = self.get_output_path()
            if output_path and os.path.dirname(output_path):
                file_path = Path(output_path)
                # create/truncate only non-image files
                if file_path.suffix not in [".png", ".jpg", ".jpeg"]:
                    os.makedirs(os.path.dirname(self.output_path), exist_ok=True)
                    with open(self.output_path, "w") as _:
                        pass
            sys.exit(0)

    def get_db_field_at_hour(self, state: "ForecastState", product_name: str) -> dict | None:
        """Fetch a pre-processed field from the fieldstore for a specific forecast run
        + hour. Used by animation frame loops and other multi-hour operations.
        Args:
            state: which run + forecast hour to read.
            product_name: The product name (e.g., "precipitation", "wind")
        Returns:
            Field dict {lat, lon, values, values2, u, v, valid_time} or None
        """
        try:
            fs = self._store
            return fs.get_field(
                state.run_date_str, state.run_id, state.fhour, product_name
            )
        except Exception as e:
            logger.debug(
                f"get_db_field_at_hour({product_name}, f{state.fhour:03d}) failed: {e}"
            )
            return None

    def regrid_for_lod(self, field, lats, lons, bbox, fill_value=np.nan):
        """Clip `field` (lat x lon 2D array) to `bbox` (lon_min, lat_min, lon_max,
        lat_max) with a 1-degree buffer, then resample onto a level-of-detail grid via
        RegularGridInterpolator. Step size is driven by self.level_of_detail (3=high/
        0.15°, 2=medium/0.20°, else low/0.25°); also sets self.lod_desc to the matching
        "high"/"medium"/"low" string as a side effect (some layers log it).

        These step sizes are tuned for a WORLD-VIEW bbox, the dominant case here (the
        frontend always projects onto a MapLibre globe; regional bboxes are supported
        but secondary) — "high" lands at ~73% of _MAX_LOD_GRID_POINTS at world scale,
        so normal operation has headroom and doesn't routinely hit the cap below.
        The cap itself still scales the step up (coarser) as a backstop if the clipped
        region is large enough to exceed the budget regardless — see that constant's
        docstring. lod_desc still reflects the CONFIGURED level; only the effective
        step size is adjusted.

        Returns (new_lats, new_lons, field_smooth) — the LOD grid axes and the
        resampled field, ready to hand to contourf.
        """
        lon_min, lat_min, lon_max, lat_max = bbox
        buf = 1.0
        lon_idx = (lons >= lon_min - buf) & (lons <= lon_max + buf)
        lat_idx = (lats >= lat_min - buf) & (lats <= lat_max + buf)
        field_clip = field[np.ix_(lat_idx, lon_idx)]
        lons_clip = lons[lon_idx]
        lats_clip = lats[lat_idx]

        if self.level_of_detail == 3:
            step = 0.15
            self.lod_desc = "high"
        elif self.level_of_detail == 2:
            step = 0.20
            self.lod_desc = "medium"
        else:
            step = 0.25
            self.lod_desc = "low"

        lat_span = lats_clip.max() - lats_clip.min()
        lon_span = lons_clip.max() - lons_clip.min()
        estimated_points = (lat_span / step + 1) * (lon_span / step + 1)
        if estimated_points > _MAX_LOD_GRID_POINTS:
            scale = (estimated_points / _MAX_LOD_GRID_POINTS) ** 0.5
            logger.debug(
                f"{self.section}: LOD grid ({int(estimated_points):,} pts) exceeds "
                f"budget ({_MAX_LOD_GRID_POINTS:,}); scaling step {step:.3f}° -> "
                f"{step * scale:.3f}°"
            )
            step *= scale

        new_lats = np.arange(lats_clip.min(), lats_clip.max() + step, step)
        new_lons = np.arange(lons_clip.min(), lons_clip.max() + step, step)

        if lats_clip[0] > lats_clip[-1]:
            lats_inc, field_inc = lats_clip[::-1], field_clip[::-1, :]
        else:
            lats_inc, field_inc = lats_clip, field_clip

        fn = RegularGridInterpolator(
            (lats_inc, lons_clip), field_inc, bounds_error=False, fill_value=fill_value
        )
        mesh_lats, mesh_lons = np.meshgrid(new_lats, new_lons, indexing="ij")
        field_smooth = fn((mesh_lats, mesh_lons))
        return new_lats, new_lons, field_smooth

    def save_key_image(
        self,
        output_path,
        cmap,
        norm,
        ticks,
        title,
        *,
        key_fontsize=8,
        labelsize=6,
        tick_format=None,
        weight=None,
        decorate=None,
    ):
        """Render a standalone horizontal colourbar key PNG at `<base>_key<ext>`.

        Shared scaffold absorbed from the near-identical save_*_key methods every
        legend-bearing layer (ozone/temperature/stormwatch/precipitation/currents/sst/
        waves) built independently. Callers supply their own cmap/norm (often reused
        from their contourf() call) plus the layer-specific ticks and title.
        `decorate`, if given, is called with the built colourbar before its title/
        ticks are styled, for callers that annotate the bar itself (e.g. waves'
        below-threshold shading).
        """
        base, ext = os.path.splitext(output_path)
        key_path = f"{base}_key{ext}"

        fig = Figure(figsize=(4, 0.3))
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        cbar = fig.colorbar(
            mpl.cm.ScalarMappable(norm=norm, cmap=cmap),
            cax=ax,
            orientation="horizontal",
            ticks=ticks,
        )
        if tick_format is not None:
            from matplotlib.ticker import FormatStrFormatter

            cbar.ax.xaxis.set_major_formatter(FormatStrFormatter(tick_format))
        if decorate is not None:
            decorate(cbar)
        cbar.ax.set_title(title, color="white", fontsize=key_fontsize, pad=2, weight=weight)
        cbar.ax.tick_params(colors="white", labelsize=labelsize)

        fig.savefig(key_path, transparent=True, bbox_inches="tight")
        fig.clear()
        logger.debug(f"{self.section}: saved key to {key_path}")

    def layer_status(self) -> dict:
        """Read-only snapshot for the Config UI's Data Status tab — the layer-task
        counterpart to CollectorBase.data_status(). Never writes; LayerBuilder records
        process_status after each render cycle (see layer_builder.py's _handle_results).

        Two shapes, depending on status_product:
          * set (multi-hour: isobars, wind, ...) — percent is the fraction of the
            forecast hours ALREADY IN THE CATALOG for status_product that are fully
            rendered (should_plot_for_hour false, i.e. every per_hour_outputs suffix
            present and fresh). should_plot_for_hour lives on MultiHourRenderMixin, not
            Updater itself — safe to call here because only subclasses that set
            status_product take this branch, and they always mix in that class too.
            Deliberately bounded by what the underlying collector
            has fetched so far, not the theoretical full forecast window — a layer being
            "100%" of what it currently has to work with is correct, not a defect, when
            the collector itself is still catching up (that's the COLLECTOR's data_status
            to report). next_update here means "next time LayerBuilder re-checks this
            task" (LAYER_CYCLE_SECONDS, its fixed fan-out cadence) rather than "next new
            forecast hour" — there's no single well-defined value for the latter since
            rendering is continuous as hours arrive, but the former is still real and
            worth showing rather than leaving blank.
          * None (single-shot: sst, clouds, markers) — the same decaying-freshness
            formula CollectorBase.data_status() uses, keyed by this task's own section
            and runs_per_day cadence. next_update falls back to an estimate (now +
            period_s) when this task hasn't completed a cycle yet, same as
            CollectorBase.data_status() — see lib/data_status.py's estimate_next_update.

        self.enabled here is the layer's frontend-visibility flag, not a render
        kill-switch — LayerBuilder.start_scheduler() dispatches every TASK_CLASSES entry
        every cycle regardless of it (gated only by the separate layer_builder.enabled
        master switch, which isn't a per-layer concept). next_update must reflect that
        real, unconditional schedule rather than reporting "disabled" for a layer that is
        in fact still being rendered in the background.
        """
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        detail = last_error
        next_update = None

        if self.status_product:
            percent = 0.0
            resolved = self.latest_store_run([self.status_product])
            if resolved:
                run_date, run_id, hours = resolved
                total = len(hours)
                rendered = 0
                for fh in hours:
                    state = ForecastState.at_hour(run_date, run_id, fh)
                    if not self.should_plot_for_hour(state, self.status_product):
                        rendered += 1
                percent = 100.0 * rendered / total if total else 0.0
                if not detail:
                    detail = f"{run_date} {run_id}Z: {rendered}/{total} hour(s) rendered"
            next_update = estimate_next_update(last_updated, LAYER_CYCLE_SECONDS, True)
        else:
            period_s = period_s_from_runs_per_day(self.settings.get("runs_per_day", 1))
            percent = freshness_percent(last_updated, period_s)
            next_update = estimate_next_update(last_updated, period_s, True)

        return build_status(
            name=self.section,
            kind="layer",
            percent=percent,
            last_updated=last_updated,
            enabled=self.enabled,
            next_update=next_update,
            detail=detail,
            status=status,
        )

    def latest_store_run(self, products):
        """Resolve the freshest run actually present in the fieldstore catalog for the
        given products, returning (run_date, run_id, hours) or None.

        Field-reading layers should resolve their run from the CATALOG, not from the
        cached GFS/RTOFS baseline. The baseline tracks what NOMADS has *published*, which
        can run ahead of what the collector has *ingested* — and, because the baseline is
        cached per process, it can also drift behind once it goes stale. Reading the
        catalog renders exactly what is on disk. Scope by `products` so independent model
        cycles (GFS 00/06/12/18 vs RTOFS "00") resolve to their own run.
        """
        try:
            store = self._store
            avail = store.field_catalog_adapter.get_latest_run_hours(products=list(products))
        except Exception as e:
            logger.warning(f"{self.section}: catalog run lookup failed: {e}")
            return None
        if not avail or not avail.get("hours"):
            return None
        return avail["run_date"], avail["run_id"], avail["hours"]

    def _resolve_forecast_state(
        self, *, baseline_key: str, resolve_fn, label: str
    ) -> "ForecastState":
        """Shared baseline-cache-or-fetch + forecast-hour math backing get_gfs_state()/
        get_rtofs_state() (~75% identical before this extraction, differing only in
        which baseline they cache/resolve and their log labels). The first updater to
        need `baseline_key` this cycle resolves it (a network sync via `resolve_fn`);
        every other updater this cycle reads the cached result from
        map_data.shared_state. Returns the resolved ForecastState (does not mutate
        self)."""
        baseline = getattr(self.map_data, "shared_state", {}).get(baseline_key)

        # ESTABLISH THE DATUM (only runs once per map refresh)
        if not baseline:
            logger.debug(f"Section {self.section} setting up {label} baseline")
            baseline = resolve_fn()
            if not baseline:
                raise RuntimeError(f"Failed to sync {label} baseline from NOMADS.")
            self.map_data.shared_state[baseline_key] = baseline
            logger.debug(
                f"{label} Baseline Synced: {baseline['date_str']} {baseline['run']}Z"
            )

        # CALCULATE THE DYNAMIC OFFSET (runs for every layer)
        now = datetime.now(timezone.utc)
        user_offset_hours = self.forecast_hour
        hours_since_run = int(
            round((now - baseline["timestamp"]).total_seconds() / 3600.0)
        )
        true_f_hour = max(0, hours_since_run + user_offset_hours)

        state = ForecastState.at_hour(baseline["date_str"], baseline["run"], true_f_hour)
        logger.debug(
            f"Section {self.section} get_{label.lower()}_state: forecast hour "
            f"{state.forecast_hour_str}; date_str {state.run_date_str}; run {state.run_id}"
        )
        return state

    def get_gfs_state(self) -> "ForecastState":
        """
        Lazy evaluation: The first updater to call this method performs a quick network
        sync to establish the GFS datum. All subsequent updaters read from memory.
        """
        from atmos_gl.lib.gfs import resolve_gfs_baseline

        return self._resolve_forecast_state(
            baseline_key="gfs_baseline", resolve_fn=resolve_gfs_baseline, label="GFS"
        )

    def get_rtofs_state(self) -> "ForecastState":
        """RTOFS (ocean) analogue of get_gfs_state, for currents and future ocean
        layers. Resolves the daily RTOFS run (its own cycle, cached separately in
        shared_state) and returns the SAME ForecastState shape get_gfs_state does, so
        render_all_hours and the fieldstore reads work unchanged — they simply operate
        on the RTOFS run.

        RTOFS is one 00Z cycle/day; 'now' is hours-since-analysis, and a per-layer
        forecast_hour offset steps forward, identical in spirit to the GFS path.
        """
        from atmos_gl.lib.rtofs import resolve_rtofs_baseline

        return self._resolve_forecast_state(
            baseline_key="rtofs_baseline", resolve_fn=resolve_rtofs_baseline, label="RTOFS"
        )


class MultiHourRenderMixin:
    """Per-forecast-hour render-caching machinery, mixed into Updater subclasses that
    render one output per forecast hour (isobars, wind, precipitation, currents, waves,
    and the scalar-field trio via ScalarFieldUpdater) rather than once per cycle.

    Single-shot layers (sst, clouds, markers) never mix this in — they render once per
    cycle, not per forecast hour, so should_plot_for_hour's per-hour freshness check and
    render_all_hours' gap-filling loop don't apply to them (architecture review
    candidate "slim Updater" — these 4 methods used to sit on Updater itself, inherited
    by every layer including ones that could never call them).

    Assumes it's mixed into an Updater subclass: uses self.output_path, self._store,
    self.per_hour_outputs, self.process_status_adapter, self.section,
    self.latest_store_run() and self.get_db_field_at_hour() (the last two stay on
    Updater itself, since markers.py — a single-shot layer — also calls
    get_db_field_at_hour directly, to sample weather at a specific hour rather than to
    render one). Updater.layer_status()'s multi-hour branch also calls
    should_plot_for_hour, but only when self.status_product is set — which only
    multi-hour subclasses do, and they always pair it with this mixin. Which forecast
    run + hour a call operates on is always passed explicitly as a ForecastState — see
    that class's docstring and CONTEXT.md's "ForecastState" entry.
    """

    def get_output_path_for_hour(self, fhour: int | str) -> str:
        """Return a per-hour output path for caching renders.

        The path is:
          {base_path}_f{fhour:03d}.png

        Example: "/path/to/precipitation_f003.png"
        """
        fhour = int(fhour)

        base, ext = os.path.splitext(self.output_path)
        return f"{base}_f{fhour:03d}{ext}"

    def publish_current_hour(self, fhour: int | str):
        """Publish the given forecast hour's render to the STABLE base filename.

        The backend caches per-hour ({base}_fNNN.png and {base}_fNNN_data.png), but the
        frontend fetches the run-agnostic base names ({base}.png and {base}_data.png) —
        it has no way to know which forecast hour is valid "now". This copies the
        per-hour outputs to those base names so the frontend always sees the latest hour.

        Copies whichever of the two artifacts exist (static PNG and/or _data.png texture),
        using atomic replace so the frontend never reads a half-written file.
        """
        fhour = int(fhour)

        base, ext = os.path.splitext(self.output_path)
        per_hour = f"{base}_f{fhour:03d}{ext}"

        pairs = [
            (per_hour, self.output_path),  # static raster
            (
                f"{base}_f{fhour:03d}_data.png",
                f"{base}_data.png",
            ),  # multi-frame texture
        ]
        import shutil

        for src, dst in pairs:
            if not os.path.exists(src):
                continue
            try:
                tmp = f"{dst}.tmp"
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)
                logger.debug(
                    f"{self.section}: published {os.path.basename(src)} -> {os.path.basename(dst)}"
                )
            except Exception as e:
                logger.warning(f"{self.section}: failed to publish {src} -> {dst}: {e}")

    def should_plot_for_hour(self, state: "ForecastState", product_name: str) -> bool:
        """Check if a per-hour output needs updating.

        Returns True if:
          - The output file doesn't exist, OR
          - The field's valid_time is newer than the output file's mtime

        Returns False if the file is already fresh. This prevents re-plotting
        when data hasn't changed. Uses the catalog metadata only (no array load).
        """
        output_path = self.get_output_path_for_hour(state.fhour)
        base, ext = os.path.splitext(output_path)

        # A complete render produces every suffix in self.per_hour_outputs. If ANY is
        # missing, re-plot to fill the gap (this is what makes "delete a _data.png to
        # force regeneration" work even when the static .png is still present).
        required_paths = []
        for suffix in self.per_hour_outputs or [".png"]:
            # ".png" -> the static per-hour file; "_data.png"/"_labels.geojson" -> base+suffix.
            required_paths.append(output_path if suffix == ext else f"{base}{suffix}")
        missing = [p for p in required_paths if not os.path.exists(p)]
        if missing:
            return True

        # All outputs exist — check freshness against when the data was written.
        # Use the static PNG's mtime as the reference (oldest-equivalent; all outputs
        # are written together in one plot() call).
        try:
            fs = self._store
            meta = fs.get_field_meta(
                state.run_date_str, state.run_id, state.fhour, product_name
            )

            if not meta or meta.get("updated_at") is None:
                # No data catalogued, don't plot (data isn't ready yet)
                return False

            # Get file's mtime and compare to when the DATA ROW was last written.
            # NOTE: use updated_at (when the field was stored), NOT valid_time (the
            # forecast's validity time, which is usually in the future and would make
            # every hour look "newer" than its PNG, forcing a re-plot every cycle).
            file_mtime = min(os.path.getmtime(p) for p in required_paths)
            file_dt = datetime.fromtimestamp(file_mtime, tz=timezone.utc)

            field_updated = meta.get("updated_at")
            if field_updated is None:
                return False

            # Ensure both are tz-aware for comparison
            if field_updated.tzinfo is None:
                field_updated = field_updated.replace(tzinfo=timezone.utc)

            # Plot if data is newer (with a 1-second tolerance for clock skew)
            return (field_updated - file_dt).total_seconds() > 1

        except Exception as e:
            logger.debug(
                f"should_plot_for_hour({product_name}, f{state.fhour:03d}) check failed: {e}"
            )
            # On error, be conservative — don't plot (file is probably fine)
            return False

    def render_all_hours(self, product_name, plot_fn, field_ready, max_hours=None):
        """Gap-filling per-hour render loop.

        The scrubber needs a rendered PNG for every forecast hour that has data, not
        just the current one. This loops over the hours present in the catalog for
        this run and plots any whose output is missing or stale (should_plot_for_hour
        decides per hour). Hours already rendered and fresh are skipped cheaply, so
        steady state is N metadata checks and zero re-renders; only newly-arrived or
        deleted hours actually plot.

        Args:
            product_name: catalog product key (e.g. "isobars").
            plot_fn: callable(field, state) that renders + writes the per-hour outputs
                     for the given ForecastState.
            field_ready: callable(field) -> bool; whether the fetched field has the
                     data this layer needs (e.g. values is not None; u/v for wind).
            max_hours: stop after actually plotting this many hours (None = drain the
                     whole backlog in one call, the original behaviour). layer_builder
                     passes 1 so one process-pool dispatch renders one hour and yields
                     the worker back to the round-robin queue, instead of one layer
                     monopolising a worker until its entire backlog is caught up
                     (architecture review candidate "interleave per-hour rendering
                     across layers").

        Returns the number of hours actually (re)plotted.
        """
        # Resolve the run from the CATALOG (what's actually ingested), not from a
        # baseline-derived state (which can be stale or ahead of the collector). Each
        # hour gets its own ForecastState, built fresh -- no instance state to save or
        # restore, so callers that resolve their own baseline state beforehand (e.g.
        # the waves heat-tile GRIB download) are unaffected by construction.
        resolved = self.latest_store_run([product_name])
        if not resolved:
            logger.info(
                f"{self.section}: no hours in catalog yet (collector may not have run)."
            )
            return 0
        run_date, run_id, hours = resolved

        plotted = 0
        examined = 0
        for fh in hours:
            examined += 1
            state = ForecastState.at_hour(run_date, run_id, fh)
            if not self.should_plot_for_hour(state, product_name):
                continue
            field = self.get_db_field_at_hour(state, product_name)
            if not field or not field_ready(field):
                continue
            try:
                plot_fn(field, state)
                plotted += 1
                # Advance last_updated as each hour lands, not just once the whole
                # cycle (every TASK_CLASSES entry) finishes — a multi-hour layer can
                # take a long time to catch up on a cold start, and the Data Status
                # UI's percent bar already reflects per-hour progress live; last_updated
                # should too instead of sitting on "never" for the whole cycle.
                self.process_status_adapter.record_process_run(self.section, "layer", success=True)
                # Publish THIS hour to the stable base filename immediately, not once
                # at the end -- with max_hours capping each call to one hour, "once at
                # the end" would mean "never" until the whole backlog drains. The
                # tradeoff: while catching up a multi-hour backlog, the stable file can
                # briefly point at an older hour than it did a moment ago (hours render
                # in ascending order) before reaching the true latest again -- accepted
                # in exchange for every layer visibly progressing instead of one at a
                # time.
                self.publish_current_hour(state.fhour)
            except Exception as e:
                logger.warning(f"{self.section}: plot f{state.fhour:03d} failed: {e}")
            if max_hours is not None and plotted >= max_hours:
                break

        stopped_early = examined < len(hours)
        if plotted:
            suffix = (
                f"({len(hours)} available, stopped early after {examined} examined)"
                if stopped_early
                else f"({len(hours)} available, {len(hours) - plotted} already fresh)"
            )
            logger.info(f"{self.section}: rendered {plotted} hour(s) {suffix}.")
        else:
            logger.debug(
                f"{self.section}: all {len(hours)} hour(s) fresh; nothing to render."
            )
        return plotted
