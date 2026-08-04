#!/usr/bin/env python3
"""Coastline land-masking, split out of tasks/common.py (architecture review
candidate "tasks/common.py bundles six unrelated concerns"): a pure geometry
function with no Updater/MapRegion coupling. `coastline_land_mask` is called
directly by sst.py/greenhouse_gases.py; currents.py and waves.py instead go through
LandMaskCache + nearest_fill_and_regrid_uv below, which used to be byte-identical
code duplicated in both files (waves.py's own comment said so: "Mirrors currents.py's
_land_mask_for exactly") -- see architecture review candidate "share currents' and
waves' land-mask/regrid pipeline".
"""
import logging

import numpy as np
from scipy.ndimage import distance_transform_edt

logger = logging.getLogger(__name__)

# Cache the unioned Natural Earth land geometry per (resolution, rounded bbox) so we
# read the shapefile and union it once, then reuse across hours and across layers
# (currents + sst share this). Module-level so it survives per-hour Updater instances.
_COAST_GEOM_CACHE = {}


def coastline_land_mask(mesh_lon, mesh_lat, lon_min, lat_min, lon_max, lat_max, res="10m"):
    """Boolean land mask (True over land) sampled at the given mesh, cut from true
    Natural Earth coastline geometry at resolution `res` ('10m' / '50m' / '110m').

    Shared by any layer that needs to remove land from an ocean field (currents, sst):
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


class LandMaskCache:
    """Global (-180/-90/180/90) coastline land mask, cached per regridded-grid shape
    for the life of one run -- shared by CurrentsUpdater and WavesUpdater, whose own
    per-class caching wrapper around coastline_land_mask() used to be byte-identical.
    `label` distinguishes each caller's log lines ("Currents"/"Waves"); a shape that
    fails once (geometry unavailable) is cached as None too, so it doesn't retry every
    call within the same run."""

    def __init__(self, label: str, res: str = "50m"):
        self._label = label
        self._res = res
        self._cache = {}

    def get(self, lat, lon, shape):
        if shape in self._cache:
            return self._cache[shape]
        mesh_lon, mesh_lat = np.meshgrid(np.asarray(lon), np.asarray(lat))
        land = coastline_land_mask(
            mesh_lon, mesh_lat, -180.0, -90.0, 180.0, 90.0, res=self._res
        )
        self._cache[shape] = land
        if land is not None:
            logger.info(
                f"{self._label}: built {shape} coastline land mask "
                f"({int(land.sum())} land cells cut)."
            )
        return land


def nearest_fill_and_regrid_uv(regrid_fn, u_native, v_native, lat_native, lon_native, step_deg):
    """Nearest-fill native NaN (land/no-data cells) in u/v, then regrid both to
    `step_deg` via `regrid_fn` (an Updater.regrid_for_lod-shaped callable) -- shared by
    CurrentsUpdater.plot() and WavesUpdater._masked_uv(), whose own copies of this
    sequence used to be byte-identical apart from the regrid step constant.

    The nearest-fill happens BEFORE regridding so bilinear interpolation doesn't bleed
    NaN outward from the coast into legitimate near-shore water -- the true coastline
    cut (LandMaskCache, applied by the caller afterward) is what actually determines
    land/sea, not this fill. A field with no valid cells at all is left untouched
    (distance_transform_edt needs SOME valid data to fill from).

    Does not mutate the caller's u_native/v_native arrays. Returns
    (new_lats, new_lons, u, v) -- callers apply any of their own steps (e.g. currents'
    speed-minimum threshold) and the land-mask cut themselves, since ordering differs
    between callers.
    """
    u_native = np.asarray(u_native, dtype=np.float32).copy()
    v_native = np.asarray(v_native, dtype=np.float32).copy()

    for native in (u_native, v_native):
        bad = ~np.isfinite(native)
        if bad.any() and not bad.all():
            idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
            native[:] = native[tuple(idx)]

    new_lats, new_lons, u = regrid_fn(
        u_native, lat_native, lon_native, fill_value=np.nan, step_override=step_deg
    )
    _, _, v = regrid_fn(
        v_native, lat_native, lon_native, fill_value=np.nan, step_override=step_deg
    )
    return new_lats, new_lons, u, v
