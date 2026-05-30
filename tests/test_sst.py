#!/usr/bin/env python3
import os
import pytest
import numpy as np
import xarray as xr
from worldmap.tasks.sst import SSTUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockSSTUpdater(SSTUpdater):
    def __init__(self, config, map_data, test_nc_path, mode_override):
        super().__init__(config, map_data)
        self.nc_path = test_nc_path
        self.set_output_path()

        # Override structural modes dynamically at execution runtime
        self.mode = mode_override
        base_url = self.settings.get("url").strip('"').rstrip("/")
        self.target_url = (
            f"{base_url}/sst.day.anom.2026.nc"
            if self.mode == "anomaly"
            else f"{base_url}/sst.day.mean.2026.nc"
        )

    def generate_mock_netcdf(self):
        lats = np.arange(-89.5, 90.5, 1.0)  # length 180
        lons = np.arange(0.5, 360.5, 1.0)  # length 360
        times = [np.datetime64("2026-05-18T00:00:00")]
        var_name = "anom" if self.mode == "anomaly" else "sst"

        # Initialize an empty array with exact structural dimensions
        data_matrix = np.zeros((len(times), len(lats), len(lons)), dtype=np.float32)

        if self.mode == "anomaly":
            # Generate vectors matching spatial resolution boundaries
            lon_vec = np.sin(np.deg2rad(lons))  # length 360
            lat_vec = np.cos(np.deg2rad(lats))  # length 180

            # Use an outer product matrix multiplication to ensure correct (lat, lon) shape
            # shape result: (180, 360)
            spatial_gradient = 2.5 * np.outer(lat_vec, lon_vec)

            # Store it into our time-axis array
            data_matrix[0, :, :] = spatial_gradient
        else:
            # Absolute Mode: Warm equator grading to cold poles
            lat_profile = 30.0 * np.cos(np.deg2rad(lats))
            for idx in range(len(lons)):
                data_matrix[0, :, idx] = lat_profile

        # Assemble the dataset safely
        dataset = xr.Dataset(
            {var_name: (["time", "lat", "lon"], data_matrix)},
            coords={"time": times, "lat": lats, "lon": lons},
        )

        os.makedirs(os.path.dirname(self.nc_path), exist_ok=True)
        dataset.to_netcdf(self.nc_path)
        dataset.close()


@pytest.mark.parametrize("sst_mode", ["absolute", "anomaly"])
def test_sst_pipeline(test_env, sst_mode):
    test_nc = os.path.join(
        test_env["project_root"], "data", f"test_sst_{sst_mode}_cache.nc"
    )

    updater = MockSSTUpdater(
        test_env["config"], test_env["map_data"], test_nc, sst_mode
    )

    try:
        # 1. URL Accessibility Assertion
        assert_url_accessible(
            updater.target_url, f"NOAA OISST File for SST ({sst_mode.upper()})"
        )

        # 2. Graphics Generation Engine Validation
        updater.generate_mock_netcdf()
        updater.plot()

        assert verify_generated_image(
            updater.output_path,
            test_env["map_data"].region.target_width,
            test_env["map_data"].region.target_height,
        )
    finally:
        if os.path.exists(test_nc):
            os.remove(test_nc)
