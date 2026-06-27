#!/usr/bin/env python3
"""NASA GIBS (cloud imagery) source helpers, shared by the data_collector (which fetches
the WMS image into a file cache) and the clouds updater (which reads that cache and turns
it into a transparent overlay).

The app renders a single global view, so the cloud image is one global WMS GetMap. The
URL building (Mercator bbox math, WMS params) lives here so the collector can own the
fetch while the updater is reduced to image processing.
"""
import math
import os

MERCATOR_LAT_LIMIT = 85.0511  # just inside Google's 85.0511288 max
_R = 20037508.342789244       # 6378137 * pi — half the Mercator world span

# Single well-known global cache path (app is global; no per-region keying needed).
_CLOUDS_CACHE_FILENAME = "clouds_cache_global.jpg"

DEFAULT_LAYERS = "VIIRS_SNPP_CorrectedReflectance_TrueColor"
GLOBAL_BBOX = (-180.0, -90.0, 180.0, 90.0)


def clouds_cache_path(workdir):
    """Where the collector writes and the updater reads the raw cloud image. Keeps the
    'clouds_cache_' prefix so the housekeeper expires it like any other layer cache."""
    return os.path.join(workdir, "data", _CLOUDS_CACHE_FILENAME)


def lonlat_to_mercator_m(lon, lat):
    """WGS84 lon/lat degrees -> EPSG:3857 metres, latitude clamped to the Mercator limit
    so the poles can't produce +/-inf."""
    lat = max(-MERCATOR_LAT_LIMIT, min(MERCATOR_LAT_LIMIT, lat))
    x = lon * _R / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) * _R / math.pi
    return x, y


def build_clouds_url(base_url, width, height, time_param, bbox=GLOBAL_BBOX, layers=DEFAULT_LAYERS):
    """WMS 1.1.1 GetMap URL for the (global) cloud image at the given date and size.
    Preserves the exact request the clouds updater previously issued."""
    lon_min, lat_min, lon_max, lat_max = bbox
    x_min, y_min = lonlat_to_mercator_m(lon_min, lat_min)
    x_max, y_max = lonlat_to_mercator_m(lon_max, lat_max)
    # WMS 1.1.1 BBOX order is minx,miny,maxx,maxy.
    bbox_str = f"{x_min},{y_min},{x_max},{y_max}"
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.1.1",
        "REQUEST": "GetMap",
        "LAYERS": layers,
        "FORMAT": "image/jpeg",
        "TRANSPARENT": "FALSE",
        "STYLES": "",
        "SRS": "EPSG:3857",
        "BBOX": bbox_str,
        "WIDTH": str(int(width)),
        "HEIGHT": str(int(height)),
        "TIME": time_param,
    }
    query_string = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base_url}?{query_string}"
