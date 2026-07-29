#!/usr/bin/env python3
"""Generic cached-netCDF field loader, extracted from tasks/greenhouse_gases.py so a
second CAMS-backed render task (air_quality) doesn't have to re-implement or
copy-paste this a second time -- and so the real in-file dimension-naming bug already
fixed once here (see load_field()'s docstring) can't silently reappear in a
copy-pasted duplicate.
"""
import numpy as np
import xarray as xr
from scipy.ndimage import distance_transform_edt


def load_field(nc_path: str, var: str, *, reduce: str = "first"):
    """(matrix, lats, lons) from a cached netCDF, lon-normalised to -180..180 and
    sorted, matching the convention SSTUpdater.plot() uses for OISST.

    reduce controls how any non-lat/lon dimension collapses: "first" (a
    current-conditions cache holds a single reading) or "mean" (a baseline cache
    holds a full year at sub-daily cadence -- averaging across it gives a genuine
    annual-mean baseline rather than an arbitrarily-seasonally-biased single day, e.g.
    always comparing "today" against the baseline year's January 1st regardless of
    the current date).

    Deliberately generic about WHICH dimension that is, rather than assuming it's
    called "time": inspecting real downloaded CAMS files found the forecast dataset
    uses forecast_reference_time/forecast_period (both size 1, harmless either way)
    while the EGG4 baseline dataset uses valid_time (size 1 for a single day, but the
    real N-timestep-per-year baseline fetch needs the "mean" branch to actually fire
    on it) -- a hardcoded "time" name would silently no-op on both."""
    ds = xr.open_dataset(nc_path)
    da = ds[var]
    lat_name = "lat" if "lat" in ds.coords else "latitude"
    lon_name = "lon" if "lon" in ds.coords else "longitude"
    extra_dims = [d for d in da.dims if d not in (lat_name, lon_name)]
    if extra_dims:
        da = da.mean(dim=extra_dims) if reduce == "mean" else da.isel({d: 0 for d in extra_dims})
    raw_matrix = da.values.squeeze()
    lat_raw = ds[lat_name].values
    lon_raw = ds[lon_name].values
    ds.close()

    lon_norm = ((lon_raw + 180) % 360) - 180
    lon_sort_idx = np.argsort(lon_norm)
    lon_norm = lon_norm[lon_sort_idx]
    raw_matrix = np.asarray(raw_matrix, dtype=np.float64)[:, lon_sort_idx]

    bad = ~np.isfinite(raw_matrix)
    if bad.any() and not bad.all():
        idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
        raw_matrix = raw_matrix[tuple(idx)]

    return raw_matrix, lat_raw, lon_norm
