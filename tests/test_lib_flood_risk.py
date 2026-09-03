import os
from unittest.mock import patch

import numpy as np
import pytest
import xarray as xr

from atmos_gl.lib.flood_risk import (
    GLOFAS_LEADTIME_HOURS,
    JRC_MOSAIC_GRID_STEP_DEG,
    RETURN_PERIODS_YEARS,
    build_glofas_forecast_request,
    build_jrc_mosaic_grid,
    count_cached_jrc_tiles,
    ensemble_severity_band,
    ensure_gumbel_fit_cached,
    ensure_jrc_tile_cached,
    ensure_jrc_tile_extents_cached,
    gumbel_fit_cache_path,
    gumbel_return_period,
    gumbel_threshold_discharge,
    jrc_tile_cache_path,
    jrc_tile_extents_cache_path,
    load_gumbel_fit,
    load_jrc_hazard_mosaic,
    load_jrc_tile_index,
    regrid_nearest,
    resample_jrc_tile_onto_grid,
    save_jrc_hazard_mosaic,
    tile_dst_window,
)


# ---- gumbel_return_period / gumbel_threshold_discharge -----------------------


def test_gumbel_threshold_discharge_round_trips_through_return_period():
    """The discharge computed FOR a given return period must, fed back through
    gumbel_return_period, reproduce that same return period -- the two are meant to
    be exact inverses of each other (see module docstring's closed-form derivation)."""
    loc, scale = 500.0, 150.0
    for years in (2.0, 5.0, 20.0, 100.0):
        q = gumbel_threshold_discharge(years, loc, scale)
        rp = gumbel_return_period(np.array([q]), np.array([loc]), np.array([scale]))
        assert rp[0] == pytest.approx(years, rel=1e-6)


def test_gumbel_threshold_discharge_increases_with_return_period():
    """A rarer (higher-year) event must correspond to a larger discharge threshold."""
    loc, scale = 500.0, 150.0
    q2 = gumbel_threshold_discharge(2.0, loc, scale)
    q20 = gumbel_threshold_discharge(20.0, loc, scale)
    q100 = gumbel_threshold_discharge(100.0, loc, scale)
    assert q2 < q20 < q100


def test_gumbel_return_period_increases_with_discharge():
    loc, scale = 500.0, 150.0
    low = gumbel_return_period(np.array([400.0]), np.array([loc]), np.array([scale]))
    high = gumbel_return_period(np.array([2000.0]), np.array([loc]), np.array([scale]))
    assert high[0] > low[0]


def test_gumbel_return_period_is_nan_for_non_positive_scale():
    """Non-positive scale means no valid fit (e.g. a permanent no-flow/ocean cell) --
    must not divide by zero or a negative scale and silently return a bogus number."""
    rp = gumbel_return_period(np.array([500.0, 500.0]), np.array([100.0, 100.0]), np.array([0.0, -5.0]))
    assert np.isnan(rp[0])
    assert np.isnan(rp[1])


def test_gumbel_return_period_broadcasts_elementwise_across_arrays():
    discharge = np.array([100.0, 500.0, 2000.0])
    loc = np.full(3, 400.0)
    scale = np.full(3, 120.0)
    rp = gumbel_return_period(discharge, loc, scale)
    assert rp.shape == (3,)
    assert rp[2] > rp[1] > rp[0]


# ---- ensemble_severity_band ---------------------------------------------------


def test_ensemble_severity_band_all_members_exceed_highest_tier():
    loc = np.array([[500.0]])
    scale = np.array([[150.0]])
    q20 = gumbel_threshold_discharge(20.0, loc, scale)
    ensemble = np.full((50, 1, 1), q20 + 1000.0)  # every member well above the 20yr threshold
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 3  # index into RETURN_PERIODS_YEARS, 1-based -> 20yr
    assert fraction[0, 0] == pytest.approx(1.0)


def test_ensemble_severity_band_no_members_exceed_lowest_tier():
    loc = np.array([[500.0]])
    scale = np.array([[150.0]])
    q2 = gumbel_threshold_discharge(2.0, loc, scale)
    ensemble = np.full((50, 1, 1), q2 - 1000.0)  # every member well below even the 2yr threshold
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 0
    assert fraction[0, 0] == pytest.approx(0.0)


def test_ensemble_severity_band_picks_highest_tier_meeting_the_majority_bar():
    """A cell where >=50% of members exceed the 2yr AND 5yr thresholds, but fewer
    than half exceed 20yr, must be classified at the 5yr band (index 2) -- not the
    lowest (2yr) tier that also happens to be satisfied."""
    loc = np.array([[500.0]])
    scale = np.array([[150.0]])
    q5 = gumbel_threshold_discharge(5.0, loc, scale)
    q20 = gumbel_threshold_discharge(20.0, loc, scale)
    ensemble = np.full((50, 1, 1), (q5 + q20) / 2.0)  # exceeds 5yr, not 20yr, for every member
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 2
    assert fraction[0, 0] == pytest.approx(1.0)


def test_ensemble_severity_band_boundary_is_inclusive_at_exactly_half():
    """Exactly half the members exceeding a threshold must count as meeting that
    band (>=0.5), not fall just short of it."""
    loc = np.array([[500.0]])
    scale = np.array([[150.0]])
    q2 = gumbel_threshold_discharge(2.0, loc, scale)
    ensemble = np.empty((50, 1, 1))
    ensemble[:25] = q2 + 10.0  # above threshold
    ensemble[25:] = q2 - 10.0  # below threshold
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 1
    assert fraction[0, 0] == pytest.approx(0.5)


def test_ensemble_severity_band_shape_matches_grid_not_member_count():
    loc = np.full((4, 3), 500.0)
    scale = np.full((4, 3), 150.0)
    ensemble = np.random.default_rng(0).uniform(0.0, 1000.0, size=(50, 4, 3))
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band.shape == (4, 3)
    assert fraction.shape == (4, 3)
    assert band.dtype == np.int8


def test_ensemble_severity_band_masks_cells_with_negative_loc():
    """A negative Gumbel loc is physically impossible for river discharge (always
    >=0) -- confirmed live over Greenland's ice sheet (no real river network) that a
    regridded negative loc collapses the threshold toward/below zero, so ordinary
    discharge noise spuriously "exceeds" every tier. Such cells must render as band 0
    (no risk), not a spurious high band, regardless of how much discharge is present."""
    loc = np.array([[-50.0]])
    scale = np.array([[10.0]])
    ensemble = np.full((50, 1, 1), 5.0)  # tiny, near-zero discharge -- would otherwise exceed a negative threshold
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 0
    assert fraction[0, 0] == pytest.approx(0.0)


def test_ensemble_severity_band_masks_cells_with_non_positive_scale():
    """Mirrors gumbel_return_period's own scale<=0 guard -- a degenerate/no-flow fit
    must not be classified at all, even if raw discharge happens to be nonzero."""
    loc = np.array([[500.0]])
    scale = np.array([[0.0]])
    ensemble = np.full((50, 1, 1), 10000.0)
    band, fraction = ensemble_severity_band(ensemble, loc, scale)
    assert band[0, 0] == 0
    assert fraction[0, 0] == pytest.approx(0.0)


def test_return_periods_years_matches_glofas_official_bands():
    """Pinned so an accidental edit to the tier list is caught -- these three values
    are GloFAS's own official reporting-point classification, not arbitrary."""
    assert RETURN_PERIODS_YEARS == (2.0, 5.0, 20.0)


# ---- regrid_nearest -------------------------------------------------------------


def test_regrid_nearest_reproduces_source_values_at_matching_points():
    src_lat = np.array([0.0, 10.0, 20.0])
    src_lon = np.array([0.0, 10.0, 20.0])
    values = np.array(
        [
            [1.0, 2.0, 3.0],
            [4.0, 5.0, 6.0],
            [7.0, 8.0, 9.0],
        ]
    )
    out = regrid_nearest(values, src_lat, src_lon, src_lat, src_lon)
    assert np.array_equal(out, values)


def test_regrid_nearest_handles_descending_latitude_axis():
    """GloFAS-family grids are commonly stored north-first (descending latitude) --
    must not silently flip or misalign the field when the source axis descends."""
    src_lat = np.array([20.0, 10.0, 0.0])  # descending
    src_lon = np.array([0.0, 10.0])
    values = np.array(
        [
            [7.0, 8.0],  # lat=20
            [4.0, 5.0],  # lat=10
            [1.0, 2.0],  # lat=0
        ]
    )
    dst_lat = np.array([20.0, 10.0, 0.0])
    dst_lon = np.array([0.0, 10.0])
    out = regrid_nearest(values, src_lat, src_lon, dst_lat, dst_lon)
    assert np.array_equal(out, values)


def test_glofas_leadtime_hours_covers_a_7_day_horizon_at_24h_steps():
    """Pinned so an accidental edit is caught -- see issue #371's decision to cap the
    Live mode horizon at 7 days despite GloFAS supporting leadtimes out to 720h."""
    assert GLOFAS_LEADTIME_HOURS == ("24", "48", "72", "96", "120", "144", "168")


def test_build_glofas_forecast_request_splits_date_str_into_year_month_day():
    request = build_glofas_forecast_request("20260829")
    assert request["year"] == ["2026"]
    assert request["month"] == ["08"]
    assert request["day"] == ["29"]


def test_build_glofas_forecast_request_targets_the_full_ensemble_and_leadtime_range():
    request = build_glofas_forecast_request("20260829")
    assert request["product_type"] == ["ensemble_perturbed_forecasts"]
    assert request["variable"] == ["river_discharge_in_the_last_24_hours"]
    assert request["leadtime_hour"] == list(GLOFAS_LEADTIME_HOURS)


def test_build_glofas_forecast_request_delivers_a_bare_unarchived_netcdf():
    """GloFAS's format differs from CAMS's netcdf_zip -- must request the unarchived
    form so retrieve_with_fallback's unzip=False path is the correct one to use."""
    request = build_glofas_forecast_request("20260829")
    assert request["data_format"] == "netcdf"
    assert request["download_format"] == "unarchived"


def test_regrid_nearest_upsamples_coarse_grid_onto_finer_grid():
    """Mirrors the real use case: a coarser static grid (e.g. ETH's 0.1deg Gumbel
    fit) resampled onto a finer forecast grid (e.g. GloFAS's 0.05deg operational
    grid) -- every fine-grid point should pick up its nearest coarse-grid value."""
    src_lat = np.array([0.0, 10.0])
    src_lon = np.array([0.0, 10.0])
    values = np.array([[1.0, 2.0], [3.0, 4.0]])
    dst_lat = np.array([0.0, 2.0, 8.0, 10.0])
    dst_lon = np.array([0.0, 2.0, 8.0, 10.0])
    out = regrid_nearest(values, src_lat, src_lon, dst_lat, dst_lon)
    assert out.shape == (4, 4)
    assert out[0, 0] == 1.0  # nearest to (0, 0)
    assert out[-1, -1] == 4.0  # nearest to (10, 10)


# ---- ensure_gumbel_fit_cached / load_gumbel_fit ---------------------------------


def _make_gumbel_netcdf_bytes(tmp_path, loc_value=500.0, scale_value=100.0):
    """A tiny, genuinely valid gumbel-fit.nc-shaped netCDF (loc/scale/lat/lon), built
    via xarray rather than hand-crafted bytes -- ensure_gumbel_fit_cached's own
    corruption check opens the file with xarray, so a fixture built the same way
    exercises the real code path."""
    ds = xr.Dataset(
        {
            "loc": (("latitude", "longitude"), [[loc_value]]),
            "scale": (("latitude", "longitude"), [[scale_value]]),
        },
        coords={"latitude": [10.0], "longitude": [20.0]},
    )
    path = tmp_path / "src.nc"
    ds.to_netcdf(path)
    return path.read_bytes()


def test_ensure_gumbel_fit_cached_downloads_and_caches_when_not_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    valid_bytes = _make_gumbel_netcdf_bytes(tmp_path)

    with patch("atmos_gl.lib.gfs.download_whole", return_value=valid_bytes) as mock_download:
        path = ensure_gumbel_fit_cached()

    mock_download.assert_called_once()
    assert path == gumbel_fit_cache_path()
    assert os.path.exists(path)
    assert open(path, "rb").read() == valid_bytes


def test_ensure_gumbel_fit_cached_skips_download_when_already_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = gumbel_fit_cache_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"already-here")

    with patch("atmos_gl.lib.gfs.download_whole") as mock_download:
        path = ensure_gumbel_fit_cached()

    mock_download.assert_not_called()
    assert path == dest
    assert open(path, "rb").read() == b"already-here"


def test_ensure_gumbel_fit_cached_raises_and_leaves_no_file_on_corrupt_download(
    tmp_path, monkeypatch
):
    """A truncated/corrupt transfer must never be treated as 'already cached' by a
    later cycle's plain os.path.exists() check -- confirmed live during issue #371's
    spike that a plain download against this same host CAN silently truncate."""
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch("atmos_gl.lib.gfs.download_whole", return_value=b"not a real netcdf file"):
        with pytest.raises(Exception):
            ensure_gumbel_fit_cached()

    dest = gumbel_fit_cache_path()
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".tmp")


def test_load_gumbel_fit_returns_loc_scale_lat_lon_arrays(tmp_path):
    path = tmp_path / "gumbel.nc"
    ds = xr.Dataset(
        {
            "loc": (("latitude", "longitude"), [[500.0, 600.0]]),
            "scale": (("latitude", "longitude"), [[100.0, 120.0]]),
        },
        coords={"latitude": [10.0], "longitude": [20.0, 30.0]},
    )
    ds.to_netcdf(path)

    loc, scale, lat, lon = load_gumbel_fit(str(path))

    assert loc.shape == (1, 2)
    assert loc[0, 1] == pytest.approx(600.0)
    assert scale[0, 0] == pytest.approx(100.0)
    assert list(lat) == [10.0]
    assert list(lon) == [20.0, 30.0]


# ---- JRC Global River Flood Hazard Maps (Historical mode) ----------------------


def _make_tile_extents_geojson_bytes(features):
    import json

    return json.dumps({"type": "FeatureCollection", "features": features}).encode()


def _tile_feature(tile_id, name, lon_min, lat_min, lon_max, lat_max):
    return {
        "type": "Feature",
        "properties": {"id": tile_id, "name": name},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [lon_min, lat_max], [lon_max, lat_max],
                [lon_max, lat_min], [lon_min, lat_min], [lon_min, lat_max],
            ]],
        },
    }


def test_build_jrc_mosaic_grid_covers_the_full_globe_at_the_configured_step():
    lat, lon = build_jrc_mosaic_grid()
    assert lat.shape == (round(180.0 / JRC_MOSAIC_GRID_STEP_DEG),)
    assert lon.shape == (round(360.0 / JRC_MOSAIC_GRID_STEP_DEG),)
    assert lat[0] < 90.0 and lat[0] > lat[-1]  # descending, cell-centred (not exactly 90)
    assert lon[0] > -180.0 and lon[-1] < 180.0


def test_tile_dst_window_maps_a_10x10deg_tile_to_the_expected_cell_block():
    """A tile at the NW-most corner (N90/W180-equivalent bounds) must map to
    row/col 0 -- and every tile must be exactly (10/step_deg) cells square,
    matching JRC's own fixed tiling scheme."""
    n = round(10.0 / JRC_MOSAIC_GRID_STEP_DEG)
    row0, row1, col0, col1 = tile_dst_window((-180.0, 80.0, -170.0, 90.0))
    assert (row0, col0) == (0, 0)
    assert row1 - row0 == n
    assert col1 - col0 == n


def test_tile_dst_window_places_adjacent_tiles_without_gap_or_overlap():
    n = round(10.0 / JRC_MOSAIC_GRID_STEP_DEG)
    north = tile_dst_window((-180.0, 60.0, -170.0, 70.0))
    south = tile_dst_window((-180.0, 50.0, -170.0, 60.0))
    east = tile_dst_window((-170.0, 60.0, -160.0, 70.0))
    assert south[0] == north[1]  # south tile's rows start exactly where north's end
    assert east[2] == north[3]  # east tile's cols start exactly where north's end
    assert north[1] - north[0] == n


def _write_tiny_reclass_tif(path, values, bounds):
    """A tiny real GeoTIFF matching JRC's own reclass tile shape (uint8, nodata=255,
    north-up), sized to exactly `bounds` -- exercises resample_jrc_tile_onto_grid's
    real rasterio.warp.reproject path rather than a mocked one."""
    import rasterio
    from rasterio import Affine

    lon_min, lat_min, lon_max, lat_max = bounds
    height, width = values.shape
    transform = Affine(
        (lon_max - lon_min) / width, 0.0, lon_min,
        0.0, -(lat_max - lat_min) / height, lat_max,
    )
    with rasterio.open(
        path, "w", driver="GTiff", height=height, width=width, count=1,
        dtype="uint8", crs="EPSG:4326", transform=transform, nodata=255,
    ) as dst:
        dst.write(values, 1)


def test_resample_jrc_tile_onto_grid_takes_the_max_category_per_destination_cell(tmp_path):
    """Categorical hazard data must never let a coarse destination cell hide a
    known worst-case within it -- see the function's own docstring."""
    values = np.array([[1, 1], [1, 4]], dtype=np.uint8)
    path = str(tmp_path / "tile_max.tif")
    _write_tiny_reclass_tif(path, values, (0.0, 0.0, 10.0, 10.0))

    dst_lat = np.array([7.5, 2.5])  # 2 cells, north-first (matches the tile's own row order)
    dst_lon = np.array([2.5, 7.5])
    out = resample_jrc_tile_onto_grid(path, dst_lat, dst_lon)

    assert out.shape == (2, 2)
    assert out[1, 1] == 4  # the one cell overlapping the source's "4" pixel
    assert out[0, 0] == 1


def test_resample_jrc_tile_onto_grid_maps_native_nodata_to_zero_not_255(tmp_path):
    """255 (JRC's own nodata -- areas not modelled, not necessarily hazard-free)
    must never survive into the mosaic as a spuriously extreme category under
    max resampling."""
    values = np.full((2, 2), 255, dtype=np.uint8)
    path = str(tmp_path / "tile_nodata.tif")
    _write_tiny_reclass_tif(path, values, (0.0, 0.0, 10.0, 10.0))

    dst_lat = np.array([5.0])
    dst_lon = np.array([5.0])
    out = resample_jrc_tile_onto_grid(path, dst_lat, dst_lon)

    assert out[0, 0] == 0


def test_load_jrc_tile_index_extracts_id_name_and_bounds(tmp_path):
    features = [_tile_feature(1, "N70_W180", -180.0, 60.0, -170.0, 70.0)]
    path = tmp_path / "tile_extents.geojson"
    path.write_bytes(_make_tile_extents_geojson_bytes(features))

    tiles = load_jrc_tile_index(str(path))

    assert tiles == [{"id": 1, "name": "N70_W180", "bounds": (-180.0, 60.0, -170.0, 70.0)}]


def test_ensure_jrc_tile_extents_cached_downloads_and_caches_when_not_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    features = [_tile_feature(1, "N70_W180", -180.0, 60.0, -170.0, 70.0)]
    valid_bytes = _make_tile_extents_geojson_bytes(features)

    with patch("atmos_gl.lib.gfs.download_whole", return_value=valid_bytes) as mock_download:
        path = ensure_jrc_tile_extents_cached()

    mock_download.assert_called_once()
    assert path == jrc_tile_extents_cache_path()
    assert os.path.exists(path)


def test_ensure_jrc_tile_extents_cached_skips_download_when_already_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = jrc_tile_extents_cache_path()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b'{"type": "FeatureCollection", "features": []}')

    with patch("atmos_gl.lib.gfs.download_whole") as mock_download:
        ensure_jrc_tile_extents_cached()

    mock_download.assert_not_called()


def test_ensure_jrc_tile_extents_cached_raises_and_leaves_no_file_on_corrupt_download(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch("atmos_gl.lib.gfs.download_whole", return_value=b"not json at all"):
        with pytest.raises(Exception):
            ensure_jrc_tile_extents_cached()

    dest = jrc_tile_extents_cache_path()
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".tmp")


def test_ensure_jrc_tile_extents_cached_raises_on_an_empty_feature_list(tmp_path, monkeypatch):
    """A response with no features at all is treated as corrupt, same as invalid
    JSON -- an empty tile index would silently make Historical mode's mosaic
    permanently empty rather than retrying."""
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch("atmos_gl.lib.gfs.download_whole", return_value=_make_tile_extents_geojson_bytes([])):
        with pytest.raises(Exception):
            ensure_jrc_tile_extents_cached()


def test_ensure_jrc_tile_cached_downloads_and_caches_when_not_present(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    tif_path = tmp_path / "src.tif"
    _write_tiny_reclass_tif(str(tif_path), np.array([[1, 2]], dtype=np.uint8), (0.0, 0.0, 10.0, 10.0))
    valid_bytes = tif_path.read_bytes()

    with patch("atmos_gl.lib.gfs.download_whole", return_value=valid_bytes) as mock_download:
        path = ensure_jrc_tile_cached(1, "N70_W180")

    mock_download.assert_called_once()
    assert path == jrc_tile_cache_path(1, "N70_W180")
    assert os.path.exists(path)


def test_ensure_jrc_tile_cached_skips_download_when_already_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    dest = jrc_tile_cache_path(1, "N70_W180")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"already-here")

    with patch("atmos_gl.lib.gfs.download_whole") as mock_download:
        path = ensure_jrc_tile_cached(1, "N70_W180")

    mock_download.assert_not_called()
    assert path == dest


def test_ensure_jrc_tile_cached_raises_and_leaves_no_file_on_corrupt_download(tmp_path, monkeypatch):
    """Same truncation risk as ensure_gumbel_fit_cached -- confirmed live during
    issue #371's spike that a 271-tile batch download against this host CAN be
    interrupted mid-transfer for individual tiles."""
    monkeypatch.setenv("HOME", str(tmp_path))

    with patch("atmos_gl.lib.gfs.download_whole", return_value=b"not a real tif"):
        with pytest.raises(Exception):
            ensure_jrc_tile_cached(1, "N70_W180")

    dest = jrc_tile_cache_path(1, "N70_W180")
    assert not os.path.exists(dest)
    assert not os.path.exists(dest + ".tmp")


def test_count_cached_jrc_tiles_is_none_before_the_tile_index_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert count_cached_jrc_tiles() is None


def test_count_cached_jrc_tiles_counts_only_tiles_present_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    features = [
        _tile_feature(1, "N70_W180", -180.0, 60.0, -170.0, 70.0),
        _tile_feature(2, "N60_W180", -180.0, 50.0, -170.0, 60.0),
    ]
    index_path = jrc_tile_extents_cache_path()
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "wb") as f:
        f.write(_make_tile_extents_geojson_bytes(features))

    only_tile_1 = jrc_tile_cache_path(1, "N70_W180")
    os.makedirs(os.path.dirname(only_tile_1), exist_ok=True)
    with open(only_tile_1, "wb") as f:
        f.write(b"fake")

    assert count_cached_jrc_tiles() == (1, 2)


def test_save_and_load_jrc_hazard_mosaic_round_trips(tmp_path):
    path = str(tmp_path / "mosaic.nc")
    band = np.array([[0, 1], [2, 4]], dtype=np.uint8)
    lat = np.array([10.0, 0.0])
    lon = np.array([20.0, 30.0])

    save_jrc_hazard_mosaic(path, band, lat, lon)
    loaded_band, loaded_lat, loaded_lon = load_jrc_hazard_mosaic(path)

    assert np.array_equal(loaded_band, band)
    assert list(loaded_lat) == [10.0, 0.0]
    assert list(loaded_lon) == [20.0, 30.0]
    assert not os.path.exists(path + ".tmp")
