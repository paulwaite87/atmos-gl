#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Response, Depends
from sgp4.api import Satrec
from sgp4 import omm as omm_mod
from skyfield.api import EarthSatellite, load

from atmos_gl.db.satellite_adapter import SatelliteAdapter
from atmos_gl.lib.config import AtmosGLConfig

router = APIRouter(prefix="/api", tags=["Satellites"])

# Built ONCE at import; builtin avoids any runtime network fetch for leap seconds.
_TS = load.timescale(builtin=True)

# Deterministic palette so a given satellite is always the same colour.
_PALETTE = [
    "#ff4a4a",
    "#4ad6ff",
    "#9cff4a",
    "#ffb84a",
    "#c44aff",
    "#4affc4",
    "#ff4ad6",
    "#ffe84a",
    "#4a7bff",
    "#ff7a4a",
]


def _load_cfg():
    path = os.getenv("CONFIG_PATH", "./config/atmos-gl.json")
    cfg = AtmosGLConfig(path)
    cfg.load()
    return cfg


def get_satellite_adapter() -> SatelliteAdapter:
    return SatelliteAdapter()


def _split_dateline(coords):
    """coords: list of [lon,lat]. Break at antimeridian crossings; keep segments >=2 pts."""
    if not coords:
        return []
    segs, cur = [], [coords[0]]
    for i in range(len(coords) - 1):
        if abs(coords[i + 1][0] - coords[i][0]) > 180:
            segs.append(cur)
            cur = []
        cur.append(coords[i + 1])
    segs.append(cur)
    return [s for s in segs if len(s) >= 2]


def _line(seg, ftype, row, color):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": seg},
        "properties": {
            "feature_type": ftype,
            "norad_id": row["norad_id"],
            "name": row["name"],
            "color": color,
        },
    }


def _point(pt, row, color, alt_km):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": pt},
        "properties": {
            "feature_type": "POSITION",
            "norad_id": row["norad_id"],
            "name": row["name"],
            "color": color,
            "alt_km": round(alt_km),
        },
    }


@router.get("/satellites/geojson")
def satellites_geojson(satellite_adapter: SatelliteAdapter = Depends(get_satellite_adapter)):
    s = _load_cfg().get_section("satellites")

    names = list(s.get("sat_names", []) or [])
    extra = s.get("extra_satellite_names", "")
    if isinstance(extra, str) and extra.strip():
        names += [n.strip() for n in extra.split(",") if n.strip()]

    past = int(s.get("past_minutes", 90))
    future = int(s.get("future_minutes", 90))
    step = max(10, int(s.get("step_seconds", 30)))
    user_color = (s.get("color") or "").strip()

    rows = satellite_adapter.get_satellites_by_names(names)
    if not rows:
        return Response(
            '{"type":"FeatureCollection","features":[]}', media_type="application/json"
        )

    # One shared time grid for every satellite.
    now = datetime.now(timezone.utc)
    n = int((past + future) * 60 / step)
    times = [
        now - timedelta(minutes=past) + timedelta(seconds=i * step)
        for i in range(n + 1)
    ]
    t = _TS.from_datetimes(times)
    now_idx = int(past * 60 / step)

    features = []
    for row in rows:
        try:
            rec = Satrec()
            omm_mod.initialize(rec, row["omm"])
            sat = EarthSatellite.from_satrec(rec, _TS)
            sp = sat.at(t).subpoint()
            lons = sp.longitude.degrees
            lats = sp.latitude.degrees
            elev = sp.elevation.km
        except Exception:
            continue  # bad/expired element set — skip this object

        coords = [[float(lon), float(lat)] for lon, lat in zip(lons, lats)]
        color = user_color or _PALETTE[int(row["norad_id"]) % len(_PALETTE)]

        for seg in _split_dateline(coords[: now_idx + 1]):
            features.append(_line(seg, "TRACK_PAST", row, color))
        for seg in _split_dateline(coords[now_idx:]):
            features.append(_line(seg, "TRACK_FUTURE", row, color))
        features.append(_point(coords[now_idx], row, color, float(elev[now_idx])))

    fc = {"type": "FeatureCollection", "features": features}
    return Response(json.dumps(fc), media_type="application/json")
