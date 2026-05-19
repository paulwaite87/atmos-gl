#!/usr/bin/env python3
import os
import sys
import numpy as np
import xarray as xr
from unittest.mock import patch

# Append project root to path to ensure clean internal imports
sys.path.insert(0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), ".."))))

from worldmap.tasks.wind import WindUpdater
from tests.common import test_env, check_url_accessibility, verify_generated_image


class MockWindUpdater(WindUpdater):
    """Subclass of production WindUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data, test_output_path):
        super().__init__(config, map_data)
        self.output_path = test_output_path
        self.grib_path = "dummy_gfs_wind.grib2"


def generate_mock_wind_dataset():
    """Generates a mock dataset matching the GFS atmospheric matrix layout for wind components."""
    # GFS 0.25-degree coordinate arrays
    lats = np.arange(90.0, -90.25, -0.25)
    lons = np.arange(0.0, 360.0, 0.25)

    # Initialize a baseline light breeze: U=5.0 m/s (~18 km/h), V=0.0 m/s
    u10_matrix = np.full((len(lats), len(lons)), 5.0, dtype=np.float32)
    v10_matrix = np.full((len(lats), len(lons)), 0.0, dtype=np.float32)

    # Add a localized high-wind gale signature right at Lat -18.5, Lon 160.0
    lat_idx = np.abs(lats - (-18.5)).argmin()
    lon_idx = np.abs(lons - 160.0).argmin()

    # Set localized gale force winds: U=20 m/s, V=15 m/s (Total speed ~25 m/s or 90 km/h)
    u10_matrix[lat_idx - 5:lat_idx + 5, lon_idx - 5:lon_idx + 5] = 20.0
    v10_matrix[lat_idx - 5:lat_idx + 5, lon_idx - 5:lon_idx + 5] = 15.0

    dataset = xr.Dataset(
        {
            "u10": (["latitude", "longitude"], u10_matrix),
            "v10": (["latitude", "longitude"], v10_matrix)
        },
        coords={"latitude": lats, "longitude": lons}
    )
    return dataset


def test_wind_pipeline(test_env):
    # THE FIX: Shrink the target render bounds purely for this test execution!
    # Zooming into a crisp 8x8 degree window tracking our simulated gale anomaly.
    test_env["map_data"].region.bbox = [156.0, -22.0, 164.0, -14.0]

    test_output_png = os.path.join(test_env["project_root"], "data", "test_wind_output.png")
    updater = MockWindUpdater(test_env["config"], test_env["map_data"], test_output_png)

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url")
    assert base_url, "Wind 'url' configuration is missing!"
    assert check_url_accessibility(base_url.strip(), "NOAA NOMADS GFS Hub Server")

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_wind_dataset()
    with patch("worldmap.tasks.wind.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds
        updater.plot()

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height
    )