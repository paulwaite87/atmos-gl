#!/usr/bin/env python3
"""Coastline land-masking, split out of tasks/common.py (architecture review
candidate "tasks/common.py bundles six unrelated concerns"): a pure geometry
function with no Updater/MapRegion coupling, used by waves.py and currents.py to
remove land from ocean fields.
"""
import logging

import numpy as np

logger = logging.getLogger(__name__)

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
