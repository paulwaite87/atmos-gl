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

from worldmap.tasks.isobars import IsobarUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockIsobarUpdater(IsobarUpdater):
    """Subclass of production IsobarUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        self.set_output_path()
        self.grib_path = "dummy_gfs_isobars.grib2"


def generate_mock_isobar_dataset():
    """Generates a mock dataset matching the GFS MSLP atmospheric matrix layout."""
    # GFS 0.25-degree coordinate arrays
    lats = np.arange(90.0, -90.25, -0.25)
    lons = np.arange(0.0, 360.0, 0.25)

    # Initialize a baseline pressure matrix in Pascals (e.g., 101300 Pa = 1013 hPa)
    prmsl_matrix = np.full((len(lats), len(lons)), 101300.0, dtype=np.float32)

    # Add a distinct low-pressure system (950 hPa / 95000 Pa) at Lat -30.0, Lon 100.0
    lat_idx = np.abs(lats - (-30.0)).argmin()
    lon_idx = np.abs(lons - 100.0).argmin()
    prmsl_matrix[lat_idx - 5 : lat_idx + 5, lon_idx - 5 : lon_idx + 5] = 95000.0

    dataset = xr.Dataset(
        {"prmsl": (["latitude", "longitude"], prmsl_matrix)},
        coords={"latitude": lats, "longitude": lons},
    )
    return dataset


def test_isobar_pipeline(test_env):
    # Shrink the target render bounds purely for this test execution
    # Zooming into a window tracking our simulated low-pressure matrix anomaly
    test_env["map_data"].region.bbox = [92.0, -38.0, 108.0, -22.0]

    updater = MockIsobarUpdater(test_env["config"], test_env["map_data"])

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url", "").strip('"').rstrip("/")
    assert_url_accessible(base_url, "NOAA NOMADS GFS Hub Server for Isobars")

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_isobar_dataset()
    with patch("worldmap.tasks.isobars.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds
        updater.plot()

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
    )
