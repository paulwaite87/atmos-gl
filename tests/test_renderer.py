#!/usr/bin/env python3
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.renderer import XPlanetRenderer
from tests.common import test_env


class MockXPlanetRenderer(XPlanetRenderer):
    """Subclass of production XPlanetRenderer for test consistency."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        # Ensure the 'common' settings is populated with desktop_geometry
        self.common["desktop_geometry"] = "1920x1080"

        # Ensure map_region_bbox is available (normally provided by the Updater base class)
        self.map_region_bbox = self.map_data.region.bbox
        self.centre_latitude = self.map_data.region.centre_latitude
        self.centre_longitude = self.map_data.region.centre_longitude


def test_xplanet_renderer_pipeline(test_env):
    # 1. Force configuration values to test all branches of the conf generation
    test_env["config"].update_setting("composite", "enabled", "True")
    test_env["config"].update_setting("composite", "outfile", "/dummy/composite.png")

    # Enable an individual composite layer to ensure composite_has_content = True
    test_env["config"].update_setting("sst", "enabled", "True")

    # Enable markers
    test_env["config"].update_setting("lightning", "enabled", "True")
    test_env["config"].update_setting("lightning", "outfile", "/dummy/lightning.txt")
    test_env["config"].update_setting(
        "common", "extra_marker_files", "dummy1.txt, dummy2.txt"
    )
    test_env["config"].update_setting("common", "base_filename", "testmap.jpg")

    # 2. Patch External Dependencies
    with (
        patch("worldmap.tasks.renderer.NASAGIBSDownloader") as MockDownloader,
        patch("worldmap.tasks.renderer.subprocess.run") as mock_subprocess,
        patch("worldmap.tasks.renderer.COMPOSITE_SECTIONS", ["sst"]),
        patch("worldmap.tasks.renderer.time.time", return_value=1234567890),
    ):
        # Mock the downloader to simply touch the expected file paths to satisfy os.path.exists
        mock_downloader_instance = MockDownloader.return_value

        def mock_download_region(bbox, width, height, out_path, is_night):
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as f:
                f.write("mock image data")

        mock_downloader_instance.download_region_map.side_effect = mock_download_region

        # Instantiate the updater
        updater = MockXPlanetRenderer(test_env["config"], test_env["map_data"])

        # Execute the pipeline
        updater.run()

    # 3. Verify Dynamic Configuration Generation
    conf_path = os.path.join(updater.workdir, "data", "xplanet_dynamic.conf")
    assert os.path.exists(conf_path), "xplanet_dynamic.conf was not generated!"

    with open(conf_path, "r") as f:
        conf_content = f.read()

    # Validate essential configuration keys were injected correctly
    assert "[earth]" in conf_content, "Missing earth section header[cite: 14]."
    assert "map=" in conf_content, "Missing day map assignment[cite: 14]."
    assert "night_map=" not in conf_content, "The night map should not be included[cite: 14]."
    assert "cloud_map=/dummy/composite.png" in conf_content, (
        "Composite cloud_map not injected[cite: 14]."
    )
    assert "marker_file=/dummy/lightning.txt" in conf_content, (
        "Lightning marker not injected[cite: 14]."
    )
    assert "marker_file=dummy1.txt" in conf_content, (
        "Listified marker_file 1 missing[cite: 14]."
    )
    assert "marker_file=dummy2.txt" in conf_content, (
        "Listified marker_file 2 missing[cite: 14]."
    )

    # Xplanet expects bounds in specific format: {lat_max, lon_min, lat_min, lon_max}
    bbox = updater.map_region_bbox
    expected_bounds = f"mapbounds={{{bbox[3]},{bbox[0]},{bbox[1]},{bbox[2]}}}"
    assert expected_bounds in conf_content, (
        "Mapbounds string formatting is incorrect[cite: 14]."
    )

    # 4. Verify Subprocess Execution
    mock_subprocess.assert_called_once()
    cmd_args = mock_subprocess.call_args[0][0]

    # Assert specific parameters in the command list
    assert cmd_args[0] == "xplanet", "Command must execute xplanet binary[cite: 14]."
    assert "-conf" in cmd_args and conf_path in cmd_args, (
        "Did not pass dynamic conf file[cite: 14]."
    )
    assert "-geometry" in cmd_args and "1920x1080" in cmd_args, (
        "Desktop geometry not passed correctly[cite: 14]."
    )

    # Verify the timestamped output file naming works
    expected_output = os.path.join(updater.workdir, "data", "1234567890-testmap.jpg")
    assert "-output" in cmd_args and expected_output in cmd_args, (
        "Output path mapping failed[cite: 14]."
    )
