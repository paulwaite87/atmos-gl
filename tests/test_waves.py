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

from worldmap.tasks.waves import WavesUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockWavesUpdater(WavesUpdater):
    """Subclass of production WavesUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        self.set_output_path()
        self.grib_path = "dummy_gfs_wave.grib2"


def generate_mock_wave_dataset():
    """Generates a mock dataset matching the GFS-Wave surface matrix layout."""
    # GFS 0.25-degree coordinate arrays
    lats = np.arange(90.0, -90.25, -0.25)
    lons = np.arange(0.0, 360.0, 0.25)

    # Initialize baseline Significant Wave Height (swh) and Direction (dirpw)
    # Using open water base states (e.g., 1.5m waves rolling at 270 degrees / West)
    swh_matrix = np.full((len(lats), len(lons)), 1.5, dtype=np.float32)
    dirpw_matrix = np.full((len(lats), len(lons)), 270.0, dtype=np.float32)

    # Add a major storm system (6.5m waves) at Lat -45.0, Lon 170.0
    lat_idx = np.abs(lats - (-45.0)).argmin()
    lon_idx = np.abs(lons - 170.0).argmin()

    # Inject the extreme sea state anomaly
    swh_matrix[lat_idx - 8 : lat_idx + 8, lon_idx - 8 : lon_idx + 8] = 6.5
    # Shift wave direction in the storm center to 45 degrees (North-East)
    dirpw_matrix[lat_idx - 8 : lat_idx + 8, lon_idx - 8 : lon_idx + 8] = 45.0

    dataset = xr.Dataset(
        {
            "swh": (["latitude", "longitude"], swh_matrix),
            "dirpw": (["latitude", "longitude"], dirpw_matrix),
        },
        coords={"latitude": lats, "longitude": lons},
    )
    return dataset


def test_waves_pipeline(test_env):
    # Shrink the target render bounds purely for this test execution
    # Zooming into a window tracking our simulated Southern Ocean storm matrix anomaly
    test_env["map_data"].region.bbox = [160.0, -55.0, 180.0, -35.0]

    #    test_output_png = os.path.join(test_env["project_root"], "data", "test_waves_output.png")
    updater = MockWavesUpdater(test_env["config"], test_env["map_data"])

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url").strip('"').rstrip("/")
    assert_url_accessible(base_url, "NOAA NOMADS GFS-Wave Hub Server")

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_wave_dataset()
    with patch("worldmap.tasks.waves.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds
        updater.plot()

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
    )
