#!/usr/bin/env python3
"""NASA GIBS (cloud imagery) source helpers, shared by the data_collector (which fetches
the WMS image into a file cache) and the clouds updater (which reads that cache and turns
it into a transparent overlay).

The app renders a single global view, so the cloud image is one global WMS GetMap. The
URL building (Mercator bbox math, WMS params) lives here so the collector can own the
fetch while the updater is reduced to image processing.
"""
import json
import math
import os
import re
from datetime import date, timedelta

MERCATOR_LAT_LIMIT = 85.0511  # just inside Google's 85.0511288 max
_R = 20037508.342789244       # 6378137 * pi — half the Mercator world span

# Single well-known global cache path (app is global; no per-region keying needed).
_CLOUDS_CACHE_FILENAME = "clouds_cache_global.jpg"

_NIGHT_LIGHTS_CACHE_FILENAME = "night_lights_cache_global.png"
_NIGHT_LIGHTS_DATE_FILENAME = "night_lights_cache_global.json"

NIGHT_LIGHTS_LAYER = "VIIRS_SNPP_GapFilled_BRDF_Corrected_DayNightBand_Radiance"
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


def night_lights_cache_path(workdir):
    """Where the collector writes and the updater reads the raw night-lights image."""
    return os.path.join(workdir, "data", _NIGHT_LIGHTS_CACHE_FILENAME)


def night_lights_date_path(workdir):
    return os.path.join(workdir, "data", _NIGHT_LIGHTS_DATE_FILENAME)


def write_night_lights_date(workdir, iso_date):
    """Record which GIBS date the cached image shows, next to the image."""
    with open(night_lights_date_path(workdir), "w") as f:
        json.dump({"date": iso_date}, f)


def read_night_lights_date(workdir):
    """The recorded date of the cached night-lights image, or None if absent/garbled."""
    try:
        with open(night_lights_date_path(workdir)) as f:
            return json.load(f)["date"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def layer_dates(capabilities_xml, layer):
    """Newest-first ISO dates a WMS layer offers, expanded from its time dimension
    (comma-separated `start/end/P1D` ranges and single dates)."""
    if isinstance(capabilities_xml, bytes):
        capabilities_xml = capabilities_xml.decode("utf-8", "replace")
    block = re.search(
        r"<Name>" + re.escape(layer) + r"</Name>(.*?)</Layer>", capabilities_xml, re.S
    )
    dim = re.search(r"<Dimension[^>]*>([^<]*)</Dimension>", block.group(1)) if block else None
    if not dim:
        return []
    days = set()
    for part in dim.group(1).split(","):
        bits = part.strip().split("/")
        try:
            start = date.fromisoformat(bits[0])
            end = date.fromisoformat(bits[1]) if len(bits) > 1 else start
        except ValueError:
            continue
        for n in range((end - start).days + 1):
            days.add(start + timedelta(days=n))
    return [d.isoformat() for d in sorted(days, reverse=True)]


def build_night_lights_url(base_url, width, height, time_param, layer=NIGHT_LIGHTS_LAYER):
    """Same global Mercator GetMap as the clouds image (so they overlay identically), but
    lossless PNG: the radiance is stretched later, so JPEG artefacts would show."""
    url = build_clouds_url(base_url, width, height, time_param, layers=layer)
    return url.replace("FORMAT=image/jpeg", "FORMAT=image/png")
