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


def rh_data_unpack(path):
    """RH @ 2 m -> relative humidity in percent.

    Same level slice as TMP@2m (heightAboveGround / level 2); the GRIB subset now
    carries both, so cfgrib returns a dataset with t2m AND the RH variable — we pick the
    RH one by short name (cfgrib usually names 2 m RH 'r2', sometimes 'r'). Stored as a
    scalar 'values' field; consumed only by the marker-weather sampler (no GPU layer), so
    row order is left exactly as cfgrib returns it and the sampler reads the lat axis.
    """
    ds = xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={
            "filter_by_keys": {"typeOfLevel": "heightAboveGround", "level": 2}
        },
    )
    key = next((k for k in ("r2", "r", "rh", "relative_humidity") if k in ds), None)
    if key is None:
        ds.close()
        raise KeyError("RH @2m variable not found in GRIB subset")
    rh = ds[key].values.squeeze()
    lats = ds["latitude"].values
    lons, (rh,) = _standardize_lon(ds["longitude"].values, rh)
    ds.close()
    out = _blank()
    out.update(lat=np.asarray(lats), lon=lons, values=rh)
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
    # Enforce the row-0 = north contract (see module docstring). cfgrib can return
    # latitude ascending (south-first) depending on the GRIB; sortby reorders the data
    # AND the latitude coordinate together (staying coordinate-aligned, unlike a bare
    # numpy flip), so the GPU velocity texture isn't vertically mirrored — which would
    # otherwise turn cyclonic rotation into radial divergence on the particle layer.
    ds = ds.sortby("latitude", ascending=False)
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
# IMPORTANT: the GPU fill/particle layers assume the data texture spans the FULL
# -90..+90 (their lat->row mapping is ny = 0.5 - lat/180). RTOFS data only reaches
# ~ -78.6 in the south, but we still build the target grid over the full -90..+90 so
# the texture rows line up with that assumption. The unreached -90..-78 band simply
# regrids to NaN (no source within the distance cap) -> transparent, which is correct
# (it's Antarctic continent/shelf anyway). Using -80 here instead shifted/compressed
# the whole field vertically because the texture's lat span no longer matched the VS.
CURRENTS_LAT_MIN, CURRENTS_LAT_MAX = -90.0, 90.0


def _regrid_curvilinear(lat2d, lon2d, fields, step, lat_min, lat_max, k=4, power=2.0):
    """Inverse-distance-weighted regrid of 2-D curvilinear fields onto a regular grid.

    RTOFS Latitude/Longitude are 2-D (Y,X): regular Mercator south of ~47N, tripolar
    (curvilinear) above it, longitudes running ~74..434E with a junk-filled last
    column. We:
      - unwrap longitude to [-180,180),
      - mask invalid / fill / junk-column points,
      - subsample the source ~2x (still denser than a 0.1 deg target) to keep the
        KD-tree small and memory bounded,
      - map onto the target grid by blending the k nearest source points with
        inverse-distance weights (1/dist^power), all within a distance cap so land/gaps
        stay NaN instead of smearing currents across continents.

    IDW (vs the old k=1 nearest-neighbour) removes the blockiness you get where the
    target resolution approaches the source's: NN snaps every target cell to one source
    sample, so adjacent cells share values in visible steps; IDW interpolates smoothly
    between the surrounding samples. Coverage is identical to NN (a cell is filled iff at
    least one source point is within the cap), so no new gaps appear; only the smoothness
    improves. For even more raw detail (beyond the ~0.16 deg the 2x subsample leaves),
    drop the subsample below — that is the lever, at a memory cost.

    Args:
        lat2d, lon2d: (Y,X) coordinate arrays (degrees).
        fields: dict name->(Y,X) array to regrid together (e.g. {"u":..,"v":..}).
        step, lat_min, lat_max: target grid definition.
        k: number of nearest source points to blend per target cell.
        power: inverse-distance exponent (higher = more local / nearer NN).
    Returns:
        (tlat, tlon, {name: regridded 2-D array}) on the regular grid.
    """
    lon180 = ((np.asarray(lon2d, dtype=np.float64) + 180.0) % 360.0) - 180.0
    lat = np.asarray(lat2d, dtype=np.float64)

    # Valid where every field is finite and physical, and not the junk lon column.
    valid = lon2d < 500.0
    for arr in fields.values():
        a = np.asarray(arr)
        valid = valid & np.isfinite(a) & (np.abs(a) < 100.0)

    # Subsample source (~2x) to bound the KD-tree; still denser than a 0.1 deg target.
    sub = (slice(None, None, 2), slice(None, None, 2))
    vm = valid[sub]
    src = np.column_stack([lat[sub][vm].ravel(), lon180[sub][vm].ravel()])

    # Build the target latitude axis NORTH-first by construction (row 0 = +90), so the
    # regridded rows come out north-first directly — matching the GFS layers and what the
    # GPU textures expect, without a late flipud. (Same values as an ascending arange,
    # just reversed; verified output-identical to the previous ascending+flip approach.)
    tlat = np.arange(lat_min, lat_max + step, step)[::-1]
    tlon = np.arange(-180.0, 180.0, step)
    mlat, mlon = np.meshgrid(tlat, tlon, indexing="ij")
    tgt = np.column_stack([mlat.ravel(), mlon.ravel()])

    tree = cKDTree(src)
    # k nearest within a distance cap (degrees; ~2.5 target cells). Beyond -> no source.
    kq = max(1, int(k))
    dist, idx = tree.query(tgt, k=kq, distance_upper_bound=step * 2.5)
    if kq == 1:                       # cKDTree squeezes the k axis when k==1
        dist = dist[:, None]
        idx = idx[:, None]
    # A neighbour slot is usable iff it found a real point (finite dist, in-range index).
    usable = np.isfinite(dist) & (idx < src.shape[0])
    eps = 1e-12
    w = np.where(usable, 1.0 / (np.power(dist, power) + eps), 0.0)
    wsum = w.sum(axis=1)
    hit = wsum > 0.0                  # same coverage criterion as NN
    safe_idx = np.clip(idx, 0, src.shape[0] - 1)

    out = {}
    for name, arr in fields.items():
        vals = np.asarray(arr)[sub][vm].ravel()
        contrib = np.where(usable, vals[safe_idx] * w, 0.0).sum(axis=1)
        regridded = np.where(hit, contrib / np.where(hit, wsum, 1.0), np.nan)
        regridded = regridded.reshape(mlat.shape)
        # tlat is north-first by construction, so regridded is already north-first
        # (the particle/fill GPU layers map row 0 -> +90; south-first would render
        # vertically mirrored, turning rotation into divergence).
        out[name] = regridded.astype(np.float32)
    return tlat, tlon, out


def waves_data_unpack(path):
    """GFS-Wave global 0p25 GRIB -> swell vector field (u, v) per forecast hour.

    Mirrors the original static-snapshot math: magnitude is significant wave height
    (swh), direction is primary wave direction (dirpw, or mwd as a fallback name). The
    vector points the way the swell travels: u = swh*sin(dir) east, v = swh*cos(dir)
    north. Bad / land / missing cells become NaN so encode_uv flags them transparent
    (alpha 0) and the particle layer respawns there. GFS native grid is row0=north,
    matching encode_uv, so no vertical flip is needed (unlike the south-first RTOFS).
    """
    ds = xr.open_dataset(
        path,
        engine="cfgrib",
        backend_kwargs={"filter_by_keys": {"typeOfLevel": "surface"}},
    )
    direction_key = "dirpw" if "dirpw" in ds else "mwd"
    swh = np.asarray(ds["swh"].values, dtype=np.float32).squeeze()
    mwd = np.asarray(ds[direction_key].values, dtype=np.float32).squeeze()
    lats = np.asarray(ds["latitude"].values, dtype=np.float64)
    lons, (swh, mwd) = _standardize_lon(ds["longitude"].values, swh, mwd)
    ds.close()

    bad = ~np.isfinite(swh) | (swh < 0.0) | (swh > 60.0) | ~np.isfinite(mwd)
    rad = np.radians(np.nan_to_num(mwd))
    mag = np.where(bad, np.nan, swh)
    u = mag * np.sin(rad)  # east component (m); NaN where bad -> alpha 0
    v = mag * np.cos(rad)  # north component (m)

    out = _blank()
    out.update(lat=lats, lon=lons, u=u, v=v, values=mag)
    return out


def currents_data_unpack(path):
    """RTOFS 2ds prog NetCDF -> u, v surface currents (m/s) on a regular 0.1 deg grid.

    Source variables: u_velocity / v_velocity, dims (MT, Layer, Y, X) with singleton
    MT (time) and Layer; coordinates Latitude/Longitude are 2-D curvilinear. We squeeze
    the singleton dims and regrid to a regular lat/lon grid (see _regrid_curvilinear).
    """
    ds = xr.open_dataset(path)
    u = ds["u_velocity"].values.squeeze()  # (Y,X) after dropping MT, Layer
    v = ds["v_velocity"].values.squeeze()
    lat2d = ds["Latitude"].values  # (Y,X)
    lon2d = ds["Longitude"].values  # (Y,X)
    ds.close()

    tlat, tlon, reg = _regrid_curvilinear(
        lat2d,
        lon2d,
        {"u": u, "v": v},
        CURRENTS_STEP,
        CURRENTS_LAT_MIN,
        CURRENTS_LAT_MAX,
    )
    out = _blank()
    out.update(lat=tlat, lon=tlon, u=reg["u"], v=reg["v"])
    return out


ATMOS_UNPACKERS = {
    "isobars": isobars_data_unpack,
    "precipitation": precipitation_data_unpack,
    "temperature": temperature_data_unpack,
    "humidity": rh_data_unpack,
    "ozone": ozone_data_unpack,
    "wind": wind_data_unpack,
    "stormwatch": stormwatch_data_unpack,
}

# RTOFS (ocean) products are downloaded per-file (NetCDF), not from the GFS atmos
# union, so they live in their own registry the collector's currents handler uses.
CURRENTS_UNPACKERS = {
    "currents": currents_data_unpack,
}

# GFS-Wave is GFS-cadence (same run/date/fhour as atmos) but a SEPARATE per-hour GRIB
# download (gfswave.tNNz.global.0p25.fNNN), so it gets its own registry rather than the
# atmos union.
WAVES_UNPACKERS = {
    "waves": waves_data_unpack,
}