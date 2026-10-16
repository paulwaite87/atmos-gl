#!/usr/bin/env python3
import json
import math
from datetime import datetime, timezone

from fastapi import APIRouter, Response

router = APIRouter(prefix="/api", tags=["Terminator"])

# Floor on |declination| so the terminator-latitude formula never divides by
# tan(0) at the exact equinox. 0.3 deg keeps the curve well-defined and very
# nearly vertical, which is the correct shape at equinox anyway.
_DEC_FLOOR = 0.3
_SAMPLES = 360  # longitude samples across the terminator curve


def _subsolar(dt):
    """Closed-form subsolar lat/lon (NOAA solar position). No ephemeris, no network."""
    start = datetime(dt.year, 1, 1, tzinfo=timezone.utc)
    doy = (dt - start).total_seconds() / 86400.0
    g = 2.0 * math.pi / 365.0 * doy
    decl = (
        0.006918
        - 0.399912 * math.cos(g)
        + 0.070257 * math.sin(g)
        - 0.006758 * math.cos(2 * g)
        + 0.000907 * math.sin(2 * g)
        - 0.002697 * math.cos(3 * g)
        + 0.00148 * math.sin(3 * g)
    )
    eot = 229.18 * (
        0.000075
        + 0.001868 * math.cos(g)
        - 0.032077 * math.sin(g)
        - 0.014615 * math.cos(2 * g)
        - 0.040849 * math.sin(2 * g)
    )
    lat = math.degrees(decl)
    utc_hours = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    lon = -15.0 * (utc_hours + eot / 60.0 - 12.0)
    lon = ((lon + 180.0) % 360.0) - 180.0
    return lat, lon


def _terminator_curve(slat, slon):
    """Terminator latitude per longitude. Single-valued in lon; pole-safe."""
    dec = slat
    if abs(dec) < _DEC_FLOOR:
        dec = math.copysign(_DEC_FLOOR, dec if dec != 0 else 1.0)
    tan_dec = math.tan(math.radians(dec))
    curve = []
    for i in range(_SAMPLES + 1):
        lon = -180.0 + 360.0 * i / _SAMPLES
        h = math.radians(lon - slon)
        curve.append([lon, math.degrees(math.atan(-math.cos(h) / tan_dec))])
    return curve, dec


@router.get("/terminator/geojson")
def terminator_geojson():
    slat, slon = _subsolar(datetime.now(timezone.utc))
    curve, dec = _terminator_curve(slat, slon)

    # Summer pole is lit; close the night polygon over the opposite (dark) pole.
    dark_pole = -90.0 if dec > 0 else 90.0
    ring = curve + [[180.0, dark_pole], [-180.0, dark_pole], curve[0]]

    fc = {
        "type": "FeatureCollection",
        # Subsolar point (lat/lon of the sun directly overhead). Exposed so the frontend
        # can align the globe's atmosphere/light to the real sun without recomputing it —
        # one source of truth shared with the terminator shadow.
        "subsolar": {"lat": round(slat, 4), "lon": round(slon, 4)},
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"feature_type": "NIGHT"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": curve},
                "properties": {"feature_type": "TERMINATOR"},
            },
        ],
    }
    return Response(json.dumps(fc), media_type="application/json")
