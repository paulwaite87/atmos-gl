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

from worldmap.tasks.temperature import TemperatureUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockTemperatureUpdater(TemperatureUpdater):
    """Subclass of production TemperatureUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data, mode_override):
        super().__init__(config, map_data)
        self.set_output_path()
        self.grib_path = "dummy_gfs_temp.grib2"

        # Override dynamic configuration modes at runtime
        self.settings["mode"] = mode_override


def generate_mock_temperature_dataset():
    """Generates a mock dataset matching the GFS atmospheric matrix layout."""
    # GFS 0.25-degree coordinate arrays
    lats = np.arange(90.0, -90.25, -0.25)
    lons = np.arange(0.0, 360.0, 0.25)

    # Initialize a baseline temperature matrix in Kelvin (e.g., 285K or ~12°C)
    t2m_matrix = np.full((len(lats), len(lons)), 285.15, dtype=np.float32)

    # Add a distinct cold pool/anomaly vector signature right at Lat -18.5, Lon 160.0
    lat_idx = np.abs(lats - (-18.5)).argmin()
    lon_idx = np.abs(lons - 160.0).argmin()
    # Drops the temperature cells around this coordinate index to near freezing (273.15K)
    t2m_matrix[lat_idx - 5 : lat_idx + 5, lon_idx - 5 : lon_idx + 5] = 273.15

    dataset = xr.Dataset(
        {"t2m": (["latitude", "longitude"], t2m_matrix)},
        coords={"latitude": lats, "longitude": lons},
    )
    return dataset


@pytest.mark.parametrize("temp_mode", ["absolute", "anomaly"])
def test_temperature_pipeline(test_env, temp_mode):
    # THE FIX: Shrink the target render bounds purely for this test execution!
    # Zooming into a crisp 8x8 degree window tracking our simulated matrix anomaly.
    test_env["map_data"].region.bbox = [156.0, -22.0, 164.0, -14.0]

    updater = MockTemperatureUpdater(
        test_env["config"], test_env["map_data"], temp_mode
    )

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url").strip('"').rstrip("/")
    assert_url_accessible(
        base_url.strip(), "NOAA NOMADS GFS Hub Server for Air Temperature"
    )

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_temperature_dataset()
    with patch("worldmap.tasks.temperature.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds
        updater.plot()

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
    )
