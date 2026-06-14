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
import numpy as np
import xarray as xr
from scipy.ndimage import gaussian_filter
from scipy.spatial import cKDTree


logging.getLogger("cfgrib.messages").setLevel(logging.ERROR)
logging.getLogger("cfgrib.dataset").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def _blank():
    return {
        "lat": None,
        "lon": None,
        "values": None,
        "values2": None,
        "u": None,
        "v": None,
    }


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
        path,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "meanSea", "shortName": "prmsl"}
        },
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
        path,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "surface", "shortName": "prate"}
        },
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
        path,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}
        },
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
        path,
        engine="cfgrib",
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
        path,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 10}
        },
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
            path,
            engine="cfgrib",
            backend_kwargs={
                "filter_by_keys": {"typeOfLevel": "surface", "shortName": short}
            },
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
# -- RTOFS currents (NetCDF, native tripolar grid -> regular lat/lon) -----------

# Target regular grid for currents. RTOFS native is ~0.08 deg on a curvilinear
# tripolar grid; we regrid to a regular 0.1 deg lat/lon so currents drop into the
# same fieldstore/encode_frames/fill-layer pipeline as every other layer. 0.1 deg
# keeps the eddy / western-boundary-current structure (the detail that makes this
# layer worth showing) at a manageable ~25 MB/hr texture.
CURRENTS_STEP = 0.1
CURRENTS_LAT_MIN, CURRENTS_LAT_MAX = -80.0, 90.0   # RTOFS covers ~ -78.6 .. 90


def _regrid_curvilinear_nn(lat2d, lon2d, fields, step, lat_min, lat_max):
    """Nearest-neighbour regrid of 2-D curvilinear fields onto a regular lat/lon grid.

    RTOFS Latitude/Longitude are 2-D (Y,X): regular Mercator south of ~47N, tripolar
    (curvilinear) above it, longitudes running ~74..434E with a junk-filled last
    column. We:
      - unwrap longitude to [-180,180),
      - mask invalid / fill / junk-column points,
      - subsample the source ~2x (still denser than a 0.1 deg target) to keep the
        KD-tree small and memory bounded,
      - nearest-neighbour map onto the target grid with a distance cap so land/gaps
        stay NaN instead of smearing currents across continents.

    TODO(perf/quality): nearest-neighbour shows slight blockiness where the target
    approaches source resolution. A linear/bicubic scattered interpolation (or a
    structured regrid like xESMF) would be smoother; deferred to keep ingest light
    and dependency-free. The fill-layer's bicubic sampling hides most of it at
    typical zooms.

    Args:
        lat2d, lon2d: (Y,X) coordinate arrays (degrees).
        fields: dict name->(Y,X) array to regrid together (e.g. {"u":..,"v":..}).
        step, lat_min, lat_max: target grid definition.
    Returns:
        (tlat, tlon, {name: regridded 2-D array}) on the regular grid.
    """
    lon180 = ((np.asarray(lon2d, dtype=np.float64) + 180.0) % 360.0) - 180.0
    lat = np.asarray(lat2d, dtype=np.float64)

    # Valid where every field is finite and physical, and not the junk lon column.
    valid = (lon2d < 500.0)
    for arr in fields.values():
        a = np.asarray(arr)
        valid = valid & np.isfinite(a) & (np.abs(a) < 100.0)

    # Subsample source (~2x) to bound the KD-tree; still denser than a 0.1 deg target.
    sub = (slice(None, None, 2), slice(None, None, 2))
    vm = valid[sub]
    src = np.column_stack([lat[sub][vm].ravel(), lon180[sub][vm].ravel()])

    tlat = np.arange(lat_min, lat_max + step, step)
    tlon = np.arange(-180.0, 180.0, step)
    mlat, mlon = np.meshgrid(tlat, tlon, indexing="ij")
    tgt = np.column_stack([mlat.ravel(), mlon.ravel()])

    tree = cKDTree(src)
    # distance_upper_bound in degrees; ~2.5 target cells. Beyond -> no source -> NaN.
    dist, idx = tree.query(tgt, k=1, distance_upper_bound=step * 2.5)
    hit = np.isfinite(dist) & (idx < src.shape[0])

    out = {}
    for name, arr in fields.items():
        vals = np.asarray(arr)[sub][vm].ravel()
        safe_idx = np.clip(idx, 0, vals.size - 1)
        regridded = np.where(hit, vals[safe_idx], np.nan).reshape(mlat.shape)
        out[name] = regridded.astype(np.float32)
    return tlat, tlon, out


def currents_data_unpack(path):
    """RTOFS 2ds prog NetCDF -> u, v surface currents (m/s) on a regular 0.1 deg grid.

    Source variables: u_velocity / v_velocity, dims (MT, Layer, Y, X) with singleton
    MT (time) and Layer; coordinates Latitude/Longitude are 2-D curvilinear. We squeeze
    the singleton dims and regrid to a regular lat/lon grid (see _regrid_curvilinear_nn).
    """
    ds = xr.open_dataset(path)
    u = ds["u_velocity"].values.squeeze()   # (Y,X) after dropping MT, Layer
    v = ds["v_velocity"].values.squeeze()
    lat2d = ds["Latitude"].values            # (Y,X)
    lon2d = ds["Longitude"].values           # (Y,X)
    ds.close()

    tlat, tlon, reg = _regrid_curvilinear_nn(
        lat2d, lon2d, {"u": u, "v": v},
        CURRENTS_STEP, CURRENTS_LAT_MIN, CURRENTS_LAT_MAX,
    )
    out = _blank()
    out.update(lat=tlat, lon=tlon, u=reg["u"], v=reg["v"])
    return out


ATMOS_UNPACKERS = {
    "isobars": isobars_data_unpack,
    "precipitation": precipitation_data_unpack,
    "temperature": temperature_data_unpack,
    "ozone": ozone_data_unpack,
    "wind": wind_data_unpack,
    "stormwatch": stormwatch_data_unpack,
}

# RTOFS (ocean) products are downloaded per-file (NetCDF), not from the GFS atmos
# union, so they live in their own registry the collector's currents handler uses.
CURRENTS_UNPACKERS = {
    "currents": currents_data_unpack,
}
