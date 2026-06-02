#!/usr/bin/env python3
import os
import sys
import pytest
import numpy as np
import xarray as xr
from unittest.mock import patch

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.precipitation import PrecipitationUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockPrecipitationUpdater(PrecipitationUpdater):
    """Subclass of production PrecipitationUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        self.grib_path = "dummy_gfs_precip.grib2"


def generate_mock_precipitation_dataset():
    """Generates a mock dataset matching the GFS matrix configuration layout."""
    lats = np.arange(90.0, -90.25, -0.25)
    lons = np.arange(0.0, 360.0, 0.25)

    prate_matrix = np.zeros((len(lats), len(lons)), dtype=np.float32)

    # Place a tiny localized storm center signature right at Lat -18.5, Lon 160.0
    lat_idx = np.abs(lats - (-18.5)).argmin()
    lon_idx = np.abs(lons - 160.0).argmin()
    prate_matrix[lat_idx - 2 : lat_idx + 2, lon_idx - 2 : lon_idx + 2] = 12.5 / 3600.0

    dataset = xr.Dataset(
        {"prate": (["latitude", "longitude"], prate_matrix)},
        coords={"latitude": lats, "longitude": lons},
    )
    return dataset


def test_precipitation_pipeline(test_env):
    # THE FIX: Shrink the target render bounds purely for this test execution!
    # Instead of the whole globe, we zoom into a tiny 8x8 degree window over the storm.
    # The production 'ds.sel()' slicing logic will now naturally protect your RAM.
    test_env["map_data"].region.bbox = [156.0, -22.0, 164.0, -14.0]

    updater = MockPrecipitationUpdater(test_env["config"], test_env["map_data"])

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url", "").strip('"').rstrip("/")
    assert_url_accessible(base_url, "NOAA NOMADS GFS Hub Server for Precipitation")

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_precipitation_dataset()
    with patch("worldmap.tasks.precipitation.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds
        updater.plot()

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
    )
