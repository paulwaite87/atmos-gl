#!/usr/bin/env python3
"""load_field: reads a variable out of a cached CAMS netCDF, collapsing whatever
non-lat/lon dimension is present. Shared by tasks/greenhouse_gases.py and
tasks/air_quality.py.

Regression coverage for a real bug found via live testing (downloading and
inspecting actual CAMS files): the forecast dataset's non-lat/lon dimensions are
named forecast_reference_time/forecast_period, and the EGG4 baseline dataset's is
valid_time -- neither is called "time". An earlier version of this function only
checked for a dimension literally named "time", which silently no-op'd on both real
datasets: harmless for a single-timestep file (plain .squeeze() still collapses a
size-1 dim), but would have produced a 3D array in production once the EGG4
collector's real full-year (2920-timestep) baseline fetch hit this code, since
reduce="mean" would never have fired on the actual valid_time dimension.
"""
import numpy as np
import xarray as xr

from atmos_gl.lib.netcdf_field import load_field


def _write_netcdf(path, *, time_dim, time_values, data_2d_per_step, lat, lon, var="tcco2"):
    data = np.stack([np.full((len(lat), len(lon)), v) for v in data_2d_per_step])
    ds = xr.Dataset(
        {var: ((time_dim, "latitude", "longitude"), data)},
        coords={time_dim: time_values, "latitude": lat, "longitude": lon},
    )
    ds.to_netcdf(path)


def test_load_field_collapses_a_non_time_named_dimension_with_reduce_first(tmp_path):
    """Regression guard: a dimension not literally named "time" (e.g. CAMS
    forecast's forecast_reference_time) must still be recognised and collapsed."""
    path = str(tmp_path / "forecast.nc")
    _write_netcdf(
        path,
        time_dim="forecast_reference_time",
        time_values=np.array(["2026-07-27"], dtype="datetime64[ns]"),
        data_2d_per_step=[42.0],
        lat=np.array([0.0, 10.0]),
        lon=np.array([0.0, 10.0]),
    )

    matrix, lats, lons = load_field(path, "tcco2", reduce="first")

    assert matrix.shape == (2, 2)
    assert np.all(matrix == 42.0)


def test_load_field_averages_a_multi_step_valid_time_dimension_with_reduce_mean(tmp_path):
    """Regression guard for the real bug: EGG4's real baseline fetch has MANY
    valid_time steps (a full year), not one -- reduce="mean" must actually average
    across all of them, not silently no-op because the dimension isn't called
    "time"."""
    path = str(tmp_path / "egg4.nc")
    _write_netcdf(
        path,
        time_dim="valid_time",
        time_values=np.array(["2003-01-01", "2003-06-01", "2003-12-31"], dtype="datetime64[ns]"),
        data_2d_per_step=[10.0, 20.0, 30.0],
        lat=np.array([0.0, 10.0]),
        lon=np.array([0.0, 10.0]),
    )

    matrix, lats, lons = load_field(path, "tcco2", reduce="mean")

    assert matrix.shape == (2, 2)
    assert np.allclose(matrix, 20.0)


def test_load_field_reduce_first_takes_only_the_first_step_not_an_average(tmp_path):
    path = str(tmp_path / "egg4.nc")
    _write_netcdf(
        path,
        time_dim="valid_time",
        time_values=np.array(["2003-01-01", "2003-06-01"], dtype="datetime64[ns]"),
        data_2d_per_step=[10.0, 30.0],
        lat=np.array([0.0, 10.0]),
        lon=np.array([0.0, 10.0]),
    )

    matrix, lats, lons = load_field(path, "tcco2", reduce="first")

    assert np.allclose(matrix, 10.0)
