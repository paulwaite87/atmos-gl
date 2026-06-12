"""Per-product GFS unpack functions.

Each `{product}_data_unpack(path)` opens a GRIB and returns the *pre-processed global
field(s)* as a uniform dict, so the collector can decode once and store numbers, leaving
plot() to just clip/render (fast). Moved here out of the individual task files (these were
the old `_load_*` helpers) so the collector and the tasks share one decode path.

Uniform return shape (all grids are 2-D, shape (nlat, nlon), row 0 = north (native GFS
order, which the WebGL data textures expect); longitudes standardised to -180..180 ascending):

    {
        "lat":     1-D float array, length nlat,
        "lon":     1-D float array, length nlon (-180..180 ascending),
        "values":  2-D primary scalar field   (or None),
        "values2": 2-D secondary scalar field (or None; e.g. stormwatch CIN),
        "u": 2-D, "v": 2-D                     (or None; vector components, e.g. wind),
    }

IMPORTANT: because the collector downloads the *union* of all atmospheric targets into one
GRIB, every unpack below filters by typeOfLevel/shortName to pick out its own variable.
The filters for precip/ozone/stormwatch are best-effort and worth verifying against a real
union file (the single-variable tasks used to open unfiltered).
"""

import logging
logging.getLogger("cfgrib.messages").setLevel(logging.ERROR)
logging.getLogger("cfgrib.dataset").setLevel(logging.ERROR)

import numpy as np
import xarray as xr
from scipy.ndimage import gaussian_filter

logger = logging.getLogger(__name__)

def _blank():
    return {"lat": None, "lon": None, "values": None, "values2": None, "u": None, "v": None}


def _standardize_lon(lons, *fields):
    """Wrap longitudes to -180..180 and sort ascending; apply the same column order to
    each 2-D field (last axis = longitude). Returns (lons_sorted, [fields_sorted...])."""
    lons = np.asarray(lons, dtype=np.float64)
    norm = ((lons + 180) % 360) - 180
    idx = np.argsort(norm)
    lons_sorted = norm[idx]
    out = [None if f is None else np.asarray(f)[..., idx] for f in fields]
    return lons_sorted, out


def isobars_data_unpack(path):
    """PRMSL -> mean sea level pressure in hPa, smoothed (sigma 1.2)."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}},
    )
    p = ds["prmsl"].values.squeeze() / 100.0
    lats = ds["latitude"].values
    lons, (p,) = _standardize_lon(ds["longitude"].values, p)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=gaussian_filter(p, sigma=1.2))
    return out


def precipitation_data_unpack(path):
    """PRATE -> precipitation rate in mm/hr, smoothed (sigma 1.0)."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface", "shortName": "prate"}},
    )
    prate = ds["prate"].values.squeeze() * 3600.0
    lats = ds["latitude"].values
    lons, (prate,) = _standardize_lon(ds["longitude"].values, prate)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=gaussian_filter(prate, sigma=1.0))
    return out


def temperature_data_unpack(path):
    """TMP @ 2 m -> temperature in degrees Celsius."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}},
    )
    key = "t2m" if "t2m" in ds else "2t"
    t = ds[key].values.squeeze() - 273.15
    lats = ds["latitude"].values
    lons, (t,) = _standardize_lon(ds["longitude"].values, t)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=t)
    return out


def ozone_data_unpack(path):
    """TOZNE -> total column ozone (raw units). Filter best-effort; verify shortName."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"shortName": "tozne"}},
    )
    var = list(ds.data_vars)[0]
    o = ds[var].values.squeeze()
    lats = ds["latitude"].values
    lons, (o,) = _standardize_lon(ds["longitude"].values, o)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=o)
    return out


def wind_data_unpack(path):
    """UGRD/VGRD @ 10 m -> u, v wind components (m/s)."""
    ds = xr.open_dataset(
        path, engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}},
    )
    u = ds["u10"].values.squeeze()
    v = ds["v10"].values.squeeze()
    lats = ds["latitude"].values
    lons, (u, v) = _standardize_lon(ds["longitude"].values, u, v)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, u=u, v=v)
    return out


def stormwatch_data_unpack(path):
    """CAPE (values) + CIN (values2), both surface (J/kg)."""
    def _one(short):
        ds = xr.open_dataset(
            path, engine="cfgrib",
            backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface", "shortName": short}},
        )
        v = ds[short].values.squeeze()
        lat = ds["latitude"].values
        lon = ds["longitude"].values
        ds.close()
        return v, lat, lon

    cape, lats, lon_raw = _one("cape")
    cin, _, _ = _one("cin")
    lons, (cape, cin) = _standardize_lon(lon_raw, cape, cin)
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=cape, values2=cin)
    return out


# Registry: product name -> unpack function. The collector iterates this for everything
# carried by the atmospheric (pgrb2.0p25) union file. waves (GFS wave product) and the
# non-GFS sources (currents=RTOFS, sst=OISST) get their own unpackers in later passes.
ATMOS_UNPACKERS = {
    "isobars": isobars_data_unpack,
    "precipitation": precipitation_data_unpack,
    "temperature": temperature_data_unpack,
    "ozone": ozone_data_unpack,
    "wind": wind_data_unpack,
    "stormwatch": stormwatch_data_unpack,
}
