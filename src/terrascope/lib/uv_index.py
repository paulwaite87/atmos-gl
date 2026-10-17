#!/usr/bin/env python3
"""UV Index (UVI) model and WHO band definitions -- pure numpy, no I/O.

The UV Index layer has no UV feed: UVI is derived per grid point and forecast hour from
GFS total column ozone, the sun's position and GFS total cloud cover.

  clear sky   UVI = 12.5 * mu0**2.42 * (O3 / 300 DU)**-1.23     (mu0 = cos solar zenith)
  cloud       x (1 - 0.75 * cloud_fraction**3.4)                 (Josefsson)

It is an approximation: no aerosol, altitude or surface-albedo terms, so it can sit
~20% off a full radiative-transfer UVI (higher at high sun in clean air).
"""

import math

import numpy as np

# Below this UVI the layer is fully transparent (night, low sun).
UV_MIN_VISIBLE = 1.0

# WHO bands: (lower bound, name, colour). Lower bounds on the continuous UVI.
UV_BANDS = (
    (0.0, "Low", "#4eb400"),
    (3.0, "Moderate", "#f7e400"),
    (6.0, "High", "#f85900"),
    (8.0, "Very high", "#d8001d"),
    (11.0, "Extreme", "#6b49c8"),
)


def uv_band(uvi):
    """WHO band name for a UVI, or None when it is below the visibility threshold."""
    if not uvi >= UV_MIN_VISIBLE:
        return None
    name = UV_BANDS[0][1]
    for lower, band, _ in UV_BANDS:
        if uvi >= lower:
            name = band
    return name


def _solar_declination_and_eot(when):
    """Declination (rad) and equation of time (minutes), Spencer's Fourier series."""
    n = when.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (when.hour - 12) / 24 + when.minute / 1440)
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
    return decl, eot


def cos_solar_zenith(lats, lons, when):
    """(len(lats), len(lons)) cos of the solar zenith angle at UTC `when`, clipped at 0
    (sun below the horizon)."""
    decl, eot = _solar_declination_and_eot(when)
    minutes = when.hour * 60 + when.minute + when.second / 60.0
    tst = (
        minutes + eot + 4.0 * np.asarray(lons, dtype=np.float64)
    )  # true solar time, min
    hour_angle = np.radians(tst / 4.0 - 180.0)
    lat = np.radians(np.asarray(lats, dtype=np.float64))[:, None]
    mu0 = (
        np.sin(lat) * math.sin(decl)
        + np.cos(lat) * math.cos(decl) * np.cos(hour_angle)[None, :]
    )
    return np.clip(mu0, 0.0, None)


def uv_index(ozone_du, cloud_pct, lats, lons, when):
    """UVI on the (lat, lon) grid. ozone_du and cloud_pct are (nlat, nlon) arrays (Dobson
    units, percent); NaN in either gives NaN so a gap never reads as a real value."""
    ozone = np.asarray(ozone_du, dtype=np.float64)
    cloud = np.clip(np.asarray(cloud_pct, dtype=np.float64) / 100.0, 0.0, 1.0)
    mu0 = cos_solar_zenith(lats, lons, when)
    with np.errstate(invalid="ignore", divide="ignore"):
        clear = 12.5 * mu0**2.42 * (ozone / 300.0) ** -1.23
        out = clear * (1.0 - 0.75 * cloud**3.4)
    out = np.where(mu0 > 0, out, 0.0)
    return np.where(np.isnan(ozone) | np.isnan(cloud), np.nan, out)
