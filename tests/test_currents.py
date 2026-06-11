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

from worldmap.tasks.currents import CurrentsUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockCurrentsUpdater(CurrentsUpdater):
    """Subclass of production CurrentsUpdater for test consistency."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        # config.setup_for_tests() modifies output paths safely
        self.nc_path = "dummy_rtofs_currents.nc"


def generate_mock_currents_dataset():
    """Generates a synthetic NetCDF dataset mimicking the NOAA RTOFS structure."""
    grid_size = 150
    y = np.arange(grid_size)
    x = np.arange(grid_size)

    # Create a localized coordinate mesh covering -50 to +50 degrees
    lons, lats = np.meshgrid(
        np.linspace(-50.0, 50.0, grid_size), np.linspace(-50.0, 50.0, grid_size)
    )

    # Base velocities: 1.0 m/s eastward, 0.5 m/s northward
    u_vel = np.full((grid_size, grid_size), 1.0, dtype=np.float32)
    v_vel = np.full((grid_size, grid_size), 0.5, dtype=np.float32)

    # Inject a simulated "land mass" (NaN values) into the center of the grid
    # to test the NearestNDInterpolator land mask fix engine.
    u_vel[60:90, 60:90] = np.nan
    v_vel[60:90, 60:90] = np.nan

    dataset = xr.Dataset(
        {
            "Longitude": (["Y", "X"], lons),
            "Latitude": (["Y", "X"], lats),
            "u_velocity": (["Y", "X"], u_vel),
            "v_velocity": (["Y", "X"], v_vel),
        },
        coords={"Y": y, "X": x},
    )
    return dataset


def test_currents_pipeline(test_env):
    # Constrain the test render bounding box to our synthetic matrix limits
    test_env["map_data"].region.bbox = [-45.0, -45.0, 45.0, 45.0]

    updater = MockCurrentsUpdater(test_env["config"], test_env["map_data"])

    # Force specific aesthetic configuration parameters to guarantee execution coverage
    updater.settings["palette"] = "electric_blue"
    updater.settings["alpha"] = "80"
    updater.settings["width_factor"] = "1.5"
    updater.settings["key_position"] = "top-left"

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url", "").strip('"').rstrip("/")
    assert_url_accessible(base_url, "NOAA NOMADS RTOFS Server for Ocean Currents")

    # 2. Graphics Generation Engine Execution via Context Injection
    mock_ds = generate_mock_currents_dataset()

    with patch("worldmap.tasks.currents.xr.open_dataset") as mock_open:
        mock_open.return_value = mock_ds

        # Execute the core streamline rendering engine directly
        updater.plot()

        # Ensure the NetCDF path was actually called by xarray
        mock_open.assert_called_once_with(updater.nc_path)

    # 3. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
    ), "Ocean Currents PNG failed structural verification!"
