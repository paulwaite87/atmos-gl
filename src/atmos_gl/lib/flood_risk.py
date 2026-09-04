#!/usr/bin/env python3
"""Shared helpers for the flood_risk layer, used by both collectors
(collectors/flood_risk.py) and the (forthcoming) updater -- same "one source of
truth for path/URL/math conventions" role lib/greenhouse_gases.py plays for the
greenhouse_gases layer.

Covers: Gumbel-distribution return-period math and ensemble severity classification
(pure, no I/O), the nearest-neighbor regrid needed to align GloFAS's 0.05deg
operational forecast grid with ETH's 0.1deg Gumbel-fit grid (confirmed live during
issue #371's spike that the two do NOT share a resolution/origin despite ETH's own
dataset description claiming otherwise), the GloFAS forecast request/cache-path
conventions Live mode's collector uses, and JRC Global River Flood Hazard Maps'
tile index/download/mosaic conventions Historical mode's collector uses.
"""
import json
import logging
import os

import numpy as np
from scipy.interpolate import RegularGridInterpolator

logger = logging.getLogger(__name__)

# ETH Research Collection's stable bitstream-content URL for gumbel-fit.nc (resolved
# via its DOI -> handle -> DSpace bundle/bitstream API chain during issue #371's
# spike; hardcoded here rather than re-resolved at runtime, same "fixed public
# dataset URL" convention as coastline.py's _GSHHG_URL).
# See https://doi.org/10.3929/ethz-b-000641667
_GUMBEL_FIT_URL = (
    "https://www.research-collection.ethz.ch/server/api/core/bitstreams/"
    "04254cb9-5816-417c-97f7-683d4ee90285/content"
)
# ETH's research-collection host 403s the default python-requests/curl User-Agent
# (confirmed live) -- a plain browser UA is sufficient to pass whatever WAF rule is
# blocking non-browser clients.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

GLOFAS_DATASET = "cems-glofas-forecast"
# Originally 1800s (issue #371's live spike: a tiny single-leadtime/small-bbox
# request queued for only ~1-2 minutes), then raised to 3600s after a near-miss
# (one observed run: 1781s), then to 7200s once 3600s was confirmed live on prod
# to be insufficient for the real global/7-leadtime/51-member download's own
# transfer time (not EWDS's queue) -- every attempt at 3600s topped out between
# ~1.1-1.45GB and timed out, never once completing. 7200s got much closer but
# still wasn't enough: one full run (2026-09-02) reached 91% (2.38G/2.61G,
# ~327KB/s sustained over its last visible window) before the bound fired --
# extrapolating that rate across the full file implies ~7900s (~2h11m) total,
# so 7200s was short by only ~12 minutes. retrieve_with_timeout() does NOT
# cancel the in-flight download on timeout (see its own docstring) and each
# attempt now downloads to its own unique tmp path (see retrieve_with_fallback's
# unzip=False branch, lib/cds_client.py) rather than resuming a shared one
# across attempts, so there is no cost to waiting longer here beyond how long a
# genuinely-still-failing attempt takes to report back -- raised to 3 hours for
# real margin above the observed ~2h11m.
#
# Now the budget for ONE leadtime-hour request rather than all 7 in one job (see
# FloodRiskLiveCollector.collect's docstring) -- confirmed live that the shared
# ECMWF/Copernicus object-store backend serving GloFAS (and, separately observed,
# CAMS) can drop the connection every few minutes for stretches, so a single
# ~2.8GB combined job risked losing ALL forecast-hour progress to one bad patch
# late in a 3-hour transfer. Left unchanged rather than scaled down by ~7x since
# there's no live evidence yet for a safe smaller per-hour bound (EWDS job-queue
# wait time doesn't scale down with file size) -- a generous shared ceiling here
# is cheap: an interrupted hour is simply retried, not re-downloaded from zero,
# since already-stored hours are skipped on the next self-gated cycle.
GLOFAS_TIMEOUT_S = 10800
# GloFAS issues one forecast run per day, but confirmed live (issue #371's spike)
# that "today"'s run isn't always published yet when this collector happens to run
# (a same-day request 400'd; the previous day's succeeded) -- same publish-lag
# fallback shape CAMS's own _CAMS_FORECAST_SEARCH_DAYS documents. A slightly wider
# margin here since EWDS's own operational notice explicitly disclaims time-critical
# availability guarantees.
GLOFAS_SEARCH_DAYS = 5
# 7-day horizon (see issue #371's Implementation Decisions) at GloFAS's own leadtime
# granularity (24h steps) -- ("24", "48", ..., "168").
GLOFAS_LEADTIME_HOURS = tuple(str(h) for h in range(24, 24 * 7 + 1, 24))

# GloFAS's own official reporting-point severity bands (2yr/5yr/20yr return-period
# exceedance) -- matches the classification the real GloFAS map viewer uses, see
# issue #371's Implementation Decisions.
RETURN_PERIODS_YEARS = (2.0, 5.0, 20.0)


def gumbel_return_period(discharge, loc, scale):
    """Return period (years) for `discharge` (m^3/s) under a Gumbel distribution
    fitted with the given `loc`/`scale` parameters, via r(q) = 1 / (1 - cdf(q)) --
    the standard formula ETH's published GloFAS threshold fit itself uses (see
    https://doi.org/10.3929/ethz-b-000641667) and reproduced here rather than
    re-derived, since the distribution fitting was already done upstream from
    1979-2015 GloFAS reanalysis discharge.

    Vectorized over arrays of any matching shape. Cells with non-positive `scale`
    (no valid fit -- e.g. permanent no-flow/ocean cells) return NaN rather than
    dividing by zero or a negative scale silently producing a bogus period.
    """
    discharge = np.asarray(discharge, dtype=np.float64)
    loc = np.asarray(loc, dtype=np.float64)
    scale = np.asarray(scale, dtype=np.float64)

    valid = scale > 0
    z = np.full(np.broadcast_shapes(discharge.shape, loc.shape, scale.shape), np.nan)
    np.divide(discharge - loc, scale, out=z, where=valid)

    cdf = np.exp(-np.exp(-z))
    exceedance = np.clip(1.0 - cdf, 1e-9, 1.0)
    return np.where(valid, 1.0 / exceedance, np.nan)


def gumbel_threshold_discharge(years, loc, scale):
    """Inverse of `gumbel_return_period`: the discharge (m^3/s) corresponding to a
    given return period `years`, i.e. q such that gumbel_return_period(q, loc,
    scale) == years. Solved directly from the Gumbel CDF rather than searched for,
    since the distribution has a closed-form inverse (the quantile function)."""
    exceedance_prob = 1.0 / years
    return loc - scale * np.log(-np.log(1.0 - exceedance_prob))


def ensemble_severity_band(ensemble_discharge, loc, scale, return_periods=RETURN_PERIODS_YEARS):
    """Classify each grid cell by the most severe GloFAS-style return-period band
    that at least half its ensemble members are forecast to exceed, plus the
    exceedance fraction for that band -- matching GloFAS's own reporting-point
    classification (2yr/5yr/20yr thresholds, banded by the fraction of ensemble
    members exceeding each).

    `ensemble_discharge` is (member, ...) -- typically (50, lat, lon). `loc`/`scale`
    are (...), matching the trailing dims -- already regridded onto
    ensemble_discharge's own grid (see `regrid_nearest`) by the caller.

    Returns (band, fraction):
      - `band`: int8 array, 0 = below the lowest threshold, else the 1-based index
        into `return_periods` for the highest threshold exceeded by >=50% of members.
      - `fraction`: float array in [0, 1], the fraction of members exceeding that
        cell's assigned band's threshold (0.0 where band is 0).

    Cells with an unreliable Gumbel fit are forced to band 0 regardless of what the
    threshold math above produces. `scale <= 0` mirrors gumbel_return_period's own
    guard, but confirmed live (flood_risk artifacts over Greenland's ice sheet and
    open ocean, unrelated to any real river) that regrid_nearest onto GloFAS's finer
    0.05deg grid can also produce a NEGATIVE `loc` at non-river cells -- physically
    impossible for river discharge (always >=0) -- which collapses
    gumbel_threshold_discharge toward/below zero, so almost any nonzero discharge
    (model noise included) spuriously "exceeds" every tier. Live sample: 24% of
    Greenland's regridded cells had negative loc, and 10% of the ice sheet was
    consequently flagged at the lowest severity band despite no real river network
    there.

    `ensemble_discharge` is deliberately left at its native dtype (float32 straight
    off the netCDF, typically) rather than upcast to float64 -- confirmed live that a
    (50, 3000, 7200) forecast array doubles from ~4.3GB to ~8.6GB under
    np.asarray(..., dtype=np.float64), which OOM-killed the data_collector process
    outright on an 11GB host right as _process_and_store() reached this call. The `>`
    comparison against threshold_q (small, loc/scale-shaped) broadcasts fine across
    mixed float32/float64 without needing the huge array pre-cast.
    """
    ensemble_discharge = np.asarray(ensemble_discharge)
    loc = np.asarray(loc, dtype=np.float64)
    scale = np.asarray(scale, dtype=np.float64)
    n_members = ensemble_discharge.shape[0]
    invalid_fit = (scale <= 0) | (loc < 0)

    band = np.zeros(ensemble_discharge.shape[1:], dtype=np.int8)
    fraction = np.zeros(ensemble_discharge.shape[1:], dtype=np.float64)

    for band_index, years in enumerate(return_periods, start=1):
        threshold_q = gumbel_threshold_discharge(years, loc, scale)
        frac_exceeding = (ensemble_discharge > threshold_q).sum(axis=0) / n_members
        meets_band = frac_exceeding >= 0.5
        band = np.where(meets_band, band_index, band)
        fraction = np.where(meets_band, frac_exceeding, fraction)

    band = np.where(invalid_fit, 0, band).astype(np.int8)
    fraction = np.where(invalid_fit, 0.0, fraction)

    return band, fraction


def regrid_nearest(values, src_lat, src_lon, dst_lat, dst_lon):
    """Nearest-neighbor resample of a (lat, lon) grid onto a different (lat, lon)
    grid -- used to align ETH's 0.1-degree Gumbel-fit grid onto GloFAS forecast's
    0.05-degree operational grid (confirmed live during issue #371's spike that the
    two grids do NOT share a resolution/origin despite ETH's own dataset description
    claiming otherwise), and reusable for JRC's 90m hazard maps onto this app's own
    working resolution.
    """
    src_lat = np.asarray(src_lat, dtype=np.float64)
    src_lon = np.asarray(src_lon, dtype=np.float64)

    lat_inc = src_lat[0] < src_lat[-1]
    lats_for_fn = src_lat if lat_inc else src_lat[::-1]
    values_for_fn = values if lat_inc else values[::-1, :]

    fn = RegularGridInterpolator(
        (lats_for_fn, src_lon), values_for_fn, method="nearest",
        bounds_error=False, fill_value=None,
    )
    mesh_lat, mesh_lon = np.meshgrid(dst_lat, dst_lon, indexing="ij")
    return fn((mesh_lat, mesh_lon))


def pad_glofas_grid_to_global_lat(values, lat):
    """Pad a GloFAS-native (lat, ...) array -- whose lat axis covers only GloFAS's own
    hydrological domain (observed live: 89.975 down to -59.975, NOT the full globe;
    GloFAS's LISFLOOD routing excludes Antarctica/deep-Southern-Ocean latitudes) --
    with NaN rows down to a full, contiguous -90..90 axis at the same lat step and
    phase as the native grid.

    Necessary because this app's WebGL data-texture shader (ui/modules/_webglfill.js's
    VS_BODY: `ny = 0.5 - lat/180.0`) assumes every encoded texture linearly spans the
    FULL -90..90 latitude range across its whole height -- true of GFS's own native
    grid and of build_jrc_mosaic_grid's explicit full-globe axis (Historical mode's
    own grid, n_lat = round(180/step)), but NOT of GloFAS's naturally ~150-degree-tall
    domain. Encoding that shorter span unpadded into a texture the shader still reads
    as 180 degrees tall stretches it to fill that height, so every row's DISPLAYED
    latitude drifts increasingly south of its TRUE latitude the further from the
    north edge -- confirmed live: real river-network shapes (e.g. New Zealand)
    rendering visibly displaced south of their true position.

    `lat` must be descending (north-first, GloFAS's own native order -- see
    regrid_nearest's docstring). Returns (padded_values, padded_lat), lat still
    descending; `values`' trailing dims (lon, or lon plus any leading ensemble/member
    axis handled by the caller per-slice) are preserved unchanged."""
    lat = np.asarray(lat, dtype=np.float64)
    values = np.asarray(values)
    step = float(lat[0] - lat[1])
    n_lat_full = round(180.0 / step)
    padded_lat = lat[0] - np.arange(n_lat_full) * step
    padded = np.full((n_lat_full,) + values.shape[1:], np.nan, dtype=values.dtype)
    padded[: len(lat)] = values
    return padded, padded_lat


def _flood_risk_cache_dir() -> str:
    """Container-local cache dir for one-time downloads -- NOT bind-mounted, same
    "ephemeral across container recreation, persistent across worker restarts"
    convention as coastline.py's _gshhg_cache_dir()."""
    return os.path.join(os.path.expanduser("~"), ".local", "share", "flood_risk")


def gumbel_fit_cache_path() -> str:
    return os.path.join(_flood_risk_cache_dir(), "gumbel-fit.nc")


def ensure_gumbel_fit_cached() -> str:
    """Download + cache ETH's Gumbel-fit threshold data (loc/scale/samples, ~10.4MB),
    if not already cached on disk. Returns the cached .nc path. Raises on failure --
    callers catch and skip this cycle, retrying next time (same graceful-fallback
    contract as coastline.py's _download_gshhg_if_needed).

    The downloaded bytes are opened once (netCDF4/HDF5) before being moved into place,
    so a truncated/corrupt transfer never gets treated as "already cached" by a later
    cycle's plain os.path.exists() check -- confirmed live during issue #371's spike
    that a plain download attempt against this same host CAN silently truncate."""
    dest = gumbel_fit_cache_path()
    if os.path.exists(dest):
        return dest

    from atmos_gl.lib.gfs import download_whole

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger.info("Flood Risk: downloading ETH GloFAS Gumbel-fit threshold data (one-time)...")
    data = download_whole(_GUMBEL_FIT_URL, timeout=180, headers={"User-Agent": _BROWSER_USER_AGENT})

    tmp_dest = f"{dest}.tmp"
    with open(tmp_dest, "wb") as f:
        f.write(data)

    try:
        import xarray as xr

        with xr.open_dataset(tmp_dest) as ds:
            for var in ("loc", "scale"):
                if var not in ds:
                    raise ValueError(f"gumbel-fit.nc missing expected variable {var!r}")
    except Exception:
        os.remove(tmp_dest)
        raise

    os.replace(tmp_dest, dest)
    logger.info(f"Flood Risk: cached Gumbel-fit data -> {dest}")
    return dest


def load_gumbel_fit(path: str):
    """(loc, scale, lat, lon) arrays from a cached gumbel-fit.nc -- loc/scale are 2D
    (lat, lon), lat/lon are the file's own native axes (0.1deg, NOT GloFAS forecast's
    0.05deg grid -- regrid_nearest() onto the forecast's own lat/lon before combining
    the two)."""
    import xarray as xr

    with xr.open_dataset(path) as ds:
        loc = ds["loc"].values.astype(np.float64)
        scale = ds["scale"].values.astype(np.float64)
        lat = ds["latitude"].values.astype(np.float64)
        lon = ds["longitude"].values.astype(np.float64)
    return loc, scale, lat, lon


def glofas_forecast_cache_path(workdir: str, leadtime_hour) -> str:
    """Cache path for ONE leadtime hour of the GloFAS ensemble discharge forecast --
    fetched, processed, and stored independently per hour (see
    FloodRiskLiveCollector.collect's docstring) rather than as a single all-7-day
    file, so a connection drop partway through only costs the hour in flight and
    already-fetched hours are never re-downloaded."""
    return os.path.join(
        workdir, "data", f"flood_risk_cache_glofas_forecast_f{int(leadtime_hour):03d}.nc"
    )


def build_glofas_forecast_request(date_str: str, leadtime_hour: str) -> dict:
    """CDS/EWDS API request for cems-glofas-forecast's full ensemble
    (product_type=ensemble_perturbed_forecasts) for ONE leadtime hour.

    Originally requested all of GLOFAS_LEADTIME_HOURS in a single job (the field
    accepts a list), but confirmed live that the shared ECMWF/Copernicus object-store
    backend can drop mid-transfer repeatedly, which made one ~2.8GB combined job an
    all-or-nothing bet against a 3-hour timeout -- see FloodRiskLiveCollector.collect's
    docstring for the per-hour split this now feeds into.

    `date_str` is "YYYYMMDD" (matching this codebase's other date_str conventions,
    e.g. resolve_gfs_baseline), split into the separate year/month/day fields this
    dataset's form requires (unlike CAMS's single combined "date" field). `leadtime_hour`
    is one entry from GLOFAS_LEADTIME_HOURS (e.g. "24")."""
    return {
        "system_version": ["operational"],
        "hydrological_model": ["lisflood"],
        "product_type": ["ensemble_perturbed_forecasts"],
        "variable": ["river_discharge_in_the_last_24_hours"],
        "year": [date_str[:4]],
        "month": [date_str[4:6]],
        "day": [date_str[6:8]],
        "leadtime_hour": [leadtime_hour],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }


# --- JRC Global River Flood Hazard Maps (Historical mode) ------------------------
#
# Open FTP, no auth, CC-BY-4.0 (confirmed live during issue #371's spike; the
# dataset's own catalogue page is WMS-only/gated, a red herring -- this FTP tree is
# the real open-data distribution point). 271 tiles globally (10x10deg WGS84
# blocks, see tile_extents.geojson), 3 arc-second (~90m) native resolution, two file
# variants per tile per return period (_depth.tif raw metres, _depth_reclass.tif
# categorical: 1:<1m, 2:1-3m, 3:3-10m, 4:>10m, nodata=255) -- only the reclass
# variant is fetched (~515MB total for RP100 across all 271 tiles), since it's
# already exactly the categorical severity classification this layer displays,
# confirmed by the dataset's own README as "consistent with the GloFAS 'Flood
# hazard 100-year return period' static layer."
# Public (no leading underscore): also used by FloodRiskHistoricalCollector.source_url()
# for the Data Status page's clickable-label link, same "hardcoded source, no
# data_collector.datasources entry" convention as StormsCollector's own URLs.
JRC_BASE_URL = "https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/CEMS-GLOFAS/flood_hazard"
# 100-year return period (see issue #371's Implementation Decisions).
_JRC_RETURN_PERIOD = "RP100"
_JRC_TILE_EXTENTS_URL = f"{JRC_BASE_URL}/tile_extents.geojson"

# Working resolution of the cached global mosaic -- matches GloFAS forecast's own
# 0.05deg operational grid (see GLOFAS_DATASET above), so Historical and Live modes
# render at a comparable pixel density despite JRC's native ~90m resolution being
# far finer than either mode needs for a global view.
JRC_MOSAIC_GRID_STEP_DEG = 0.05


def _jrc_cache_dir() -> str:
    return os.path.join(_flood_risk_cache_dir(), "jrc")


def _jrc_tiles_cache_dir() -> str:
    return os.path.join(_jrc_cache_dir(), _JRC_RETURN_PERIOD)


def jrc_tile_extents_cache_path() -> str:
    return os.path.join(_jrc_cache_dir(), "tile_extents.geojson")


def jrc_tile_cache_path(tile_id: int, tile_name: str) -> str:
    return os.path.join(
        _jrc_tiles_cache_dir(), f"ID{tile_id}_{tile_name}_{_JRC_RETURN_PERIOD}_depth_reclass.tif"
    )


def jrc_tile_download_url(tile_id: int, tile_name: str) -> str:
    return (
        f"{JRC_BASE_URL}/{_JRC_RETURN_PERIOD}/"
        f"ID{tile_id}_{tile_name}_{_JRC_RETURN_PERIOD}_depth_reclass.tif"
    )


def jrc_hazard_mosaic_cache_path(workdir: str) -> str:
    """Cache path for the final assembled global mosaic -- under {workdir}/data
    (bind-mounted, survives container recreation), unlike the raw per-tile downloads
    and tile index below (home-dir cache, GSHHG/gumbel-fit's own convention): the
    271-tile download is the expensive one-time cost worth surviving a container
    rebuild for, same reasoning as ensure_gumbel_fit_cached()."""
    return os.path.join(workdir, "data", "flood_risk_cache_jrc_hazard_mosaic.nc")


def ensure_jrc_tile_extents_cached() -> str:
    """Download + cache the 271-tile index (tiny, ~100KB), if not already cached.
    Returns the cached .geojson path. Raises on failure -- same graceful-fallback
    contract as ensure_gumbel_fit_cached()."""
    dest = jrc_tile_extents_cache_path()
    if os.path.exists(dest):
        return dest

    from atmos_gl.lib.gfs import download_whole

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    logger.info("Flood Risk: downloading JRC tile index (one-time)...")
    data = download_whole(_JRC_TILE_EXTENTS_URL, timeout=60)

    tmp_dest = f"{dest}.tmp"
    with open(tmp_dest, "wb") as f:
        f.write(data)

    try:
        with open(tmp_dest) as f:
            parsed = json.load(f)
        if not parsed.get("features"):
            raise ValueError("tile_extents.geojson has no features")
    except Exception:
        os.remove(tmp_dest)
        raise

    os.replace(tmp_dest, dest)
    logger.info(f"Flood Risk: cached JRC tile index -> {dest}")
    return dest


def load_jrc_tile_index(path: str) -> list[dict]:
    """List of {"id", "name", "bounds"} from a cached tile_extents.geojson --
    `bounds` is (lon_min, lat_min, lon_max, lat_max), derived from each feature's
    Polygon ring rather than trusted as pre-sorted corners."""
    with open(path) as f:
        parsed = json.load(f)

    tiles = []
    for feature in parsed["features"]:
        coords = feature["geometry"]["coordinates"][0]
        lons = [c[0] for c in coords]
        lats = [c[1] for c in coords]
        tiles.append(
            {
                "id": feature["properties"]["id"],
                "name": feature["properties"]["name"],
                "bounds": (min(lons), min(lats), max(lons), max(lats)),
            }
        )
    return tiles


def ensure_jrc_tile_cached(tile_id: int, tile_name: str) -> str:
    """Download + cache one JRC reclass tile (~0.1-8.5MB), if not already cached.
    Returns the cached .tif path. Raises on failure -- callers skip this tile for
    the current cycle and retry next time, same graceful-fallback contract as
    ensure_gumbel_fit_cached(); the already-cached tiles from a prior partial pass
    are untouched, so a multi-cycle download naturally resumes rather than
    restarting.

    Validated by opening with rasterio and reading a single pixel before being
    trusted as cached -- same "never cache a truncated transfer" pattern as
    ensure_gumbel_fit_cached(), needed for the same reason (this deployment
    confirmed live that plain downloads against JRC's host CAN be interrupted
    mid-transfer for a 271-tile batch)."""
    dest = jrc_tile_cache_path(tile_id, tile_name)
    if os.path.exists(dest):
        return dest

    from atmos_gl.lib.gfs import download_whole

    os.makedirs(os.path.dirname(dest), exist_ok=True)
    url = jrc_tile_download_url(tile_id, tile_name)
    data = download_whole(url, timeout=60)

    tmp_dest = f"{dest}.tmp"
    with open(tmp_dest, "wb") as f:
        f.write(data)

    try:
        import rasterio

        with rasterio.open(tmp_dest) as ds:
            ds.read(1, window=((0, 1), (0, 1)))
    except Exception:
        os.remove(tmp_dest)
        raise

    os.replace(tmp_dest, dest)
    return dest


def count_cached_jrc_tiles() -> tuple[int, int] | None:
    """(cached_count, total_tiles) for the RP100 reclass set, or None if the tile
    index itself isn't cached yet (collect() hasn't completed even its first step).
    Used by FloodRiskHistoricalCollector.data_status() to report download progress
    without touching the network."""
    index_path = jrc_tile_extents_cache_path()
    if not os.path.exists(index_path):
        return None
    tiles = load_jrc_tile_index(index_path)
    cached = sum(1 for t in tiles if os.path.exists(jrc_tile_cache_path(t["id"], t["name"])))
    return cached, len(tiles)


def build_jrc_mosaic_grid(step_deg: float = JRC_MOSAIC_GRID_STEP_DEG):
    """Full-globe cell-center (lat, lon) axes for the JRC hazard mosaic. Lat
    descends from north to south (matching JRC's own per-tile GeoTIFF row order --
    confirmed live: north-up, row 0 = top) so each tile's resampled data drops into
    the mosaic without a flip."""
    n_lat = round(180.0 / step_deg)
    n_lon = round(360.0 / step_deg)
    lat = 90.0 - step_deg / 2.0 - np.arange(n_lat) * step_deg
    lon = -180.0 + step_deg / 2.0 + np.arange(n_lon) * step_deg
    return lat, lon


def tile_dst_window(tile_bounds, step_deg: float = JRC_MOSAIC_GRID_STEP_DEG):
    """(row_start, row_end, col_start, col_end) into a build_jrc_mosaic_grid() array
    for the given tile's (lon_min, lat_min, lon_max, lat_max) bounds. JRC's own
    tiling is a fixed 10x10deg block per tile (confirmed live via tile_extents.geojson
    -- true to within ~0.0004deg of georeferencing slop, negligible at this
    0.05deg working resolution), so the window size is derived from the tiling
    scheme directly rather than re-measured per tile."""
    lon_min, _lat_min, _lon_max, lat_max = tile_bounds
    row_start = round((90.0 - lat_max) / step_deg)
    col_start = round((lon_min - (-180.0)) / step_deg)
    n = round(10.0 / step_deg)
    return row_start, row_start + n, col_start, col_start + n


def resample_jrc_tile_onto_grid(tile_path: str, dst_lat, dst_lon) -> np.ndarray:
    """Downsample one JRC reclass tile (uint8, ~90m native resolution, categories
    1-4 + nodata=255) onto the given destination cell-center axes -- typically the
    exact sub-window covering this tile's 10x10deg footprint (see tile_dst_window)
    -- via rasterio.warp.reproject with Resampling.max: hazard-CATEGORY data must
    never let a coarse working-resolution cell hide a known worst-case within it,
    same reasoning as coastline.py's _rasterize_land_mask uses Resampling-free exact
    rasterization for categorical land/sea data.

    Native nodata (255 -- areas JRC's model didn't classify, not necessarily
    hazard-free) is collapsed to 0 in the SOURCE array before reprojecting, rather
    than passed through as GDAL's src_nodata/dst_nodata: confirmed live that GDAL's
    max/min/average-family resamplers only apply nodata masking to a destination
    cell that has SOME valid contributing source pixels -- a destination cell whose
    contributing source window is entirely nodata reprojects the raw nodata value
    (255) through unmasked instead of yielding dst_nodata, silently turning
    "unclassified" into a spurious "worse-than-any-real-category" reading. Zeroing
    nodata in the source first sidesteps the bug entirely: max-resampling over
    plain 0-4 integers needs no nodata awareness at all, and 0 is also this
    mosaic's own default fill for land outside any tile's footprint, so nodata and
    "not covered" read identically downstream -- correct for a dataset that only
    classifies floodplains near modelled river networks in the first place.
    """
    import rasterio
    from rasterio import Affine
    from rasterio.warp import Resampling, reproject

    step_lat = float(dst_lat[0] - dst_lat[1]) if len(dst_lat) > 1 else JRC_MOSAIC_GRID_STEP_DEG
    step_lon = float(dst_lon[1] - dst_lon[0]) if len(dst_lon) > 1 else JRC_MOSAIC_GRID_STEP_DEG
    dst_transform = Affine(
        step_lon, 0.0, float(dst_lon[0]) - step_lon / 2.0,
        0.0, -step_lat, float(dst_lat[0]) + step_lat / 2.0,
    )
    dst = np.zeros((len(dst_lat), len(dst_lon)), dtype=np.uint8)

    with rasterio.open(tile_path) as src:
        source = src.read(1)
        if src.nodata is not None:
            source[source == src.nodata] = 0
        reproject(
            source=source,
            destination=dst,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            resampling=Resampling.max,
        )
    return dst


def save_jrc_hazard_mosaic(path: str, band: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> None:
    """Atomic write (see ensure_gumbel_fit_cached's own reasoning) of the final
    assembled global hazard-category mosaic."""
    import xarray as xr

    ds = xr.Dataset(
        {"band": (("latitude", "longitude"), band.astype(np.uint8))},
        coords={"latitude": lat.astype(np.float64), "longitude": lon.astype(np.float64)},
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    ds.to_netcdf(tmp_path)
    os.replace(tmp_path, path)


def load_jrc_hazard_mosaic(path: str):
    """(band, lat, lon) arrays from a cached hazard mosaic -- `band` is uint8,
    0 (no known hazard / not covered by any tile) through 4 (>10m depth)."""
    import xarray as xr

    with xr.open_dataset(path) as ds:
        band = ds["band"].values.astype(np.uint8)
        lat = ds["latitude"].values.astype(np.float64)
        lon = ds["longitude"].values.astype(np.float64)
    return band, lat, lon
