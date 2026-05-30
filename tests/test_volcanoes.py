#!/usr/bin/env python3
import os
import sys
import pytest
import json
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.volcanoes import VolcanoUpdater
from tests.common import test_env, assert_url_accessible


class MockVolcanoUpdater(VolcanoUpdater):
    """Subclass of production VolcanoUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        self.set_output_path()


def generate_mock_volcano_response():
    """Generates a synthetic NOAA HazEL API response to test filtering logic."""
    return {
        "count": 3,
        "items": [
            {
                "name": "Krakatau",
                "latitude": -6.102,
                "longitude": 105.423,
                "significant": True,
                "timeErupt": "D1",  # Valid Holocene date code
                "vei": 6,  # Valid VEI
            },
            {
                "name": "Yellowstone",
                "latitude": 44.43,
                "longitude": -110.67,
                "significant": True,
                "timeErupt": "U",  # INVALID: Unknown/ancient eruption date
                "vei": 8,
            },
            {
                "name": "Small Cone",
                "latitude": 10.0,
                "longitude": 20.0,
                "significant": False,
                "timeErupt": "D1",
                "vei": 2,  # INVALID: VEI too low (below 5)
            },
        ],
    }


def test_volcano_pipeline(test_env):
    updater = MockVolcanoUpdater(test_env["config"], test_env["map_data"])

    # Force specific configuration parameters to test the filtering engine
    updater.settings["vei_min"] = "5"
    updater.settings["erupt_date_codes"] = '["D1"]'
    updater.settings["marker_color"] = "red"
    updater.settings["marker_symbol"] = "volcano.png"
    updater.settings["significant_only"] = "False"

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url").strip('"').rstrip("/")
    assert_url_accessible(base_url.strip(), "NOAA HazEL API for Volcano Data")

    # 2. Mocking the HTTP request
    # Create a mock response object that mimics urlopen's context manager interface
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        generate_mock_volcano_response()
    ).encode("utf-8")
    mock_response.__enter__.return_value = mock_response

    # Execute the pipeline with the mocked HTTP payload
    with patch("worldmap.tasks.volcanoes.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = mock_response
        updater.run()

    # 3. File Generation & Format Verification
    assert os.path.exists(updater.output_path), (
        "Volcano output text file was not generated!"
    )

    with open(updater.output_path, "r") as f:
        lines = f.readlines()

    # Verify that ONLY Krakatau passed the filter (1 valid out of 3 total)
    assert len(lines) == 1, f"Expected 1 volcano to pass filters, but got {len(lines)}."

    # Verify the formatting specifically matches what xplanet expects
    expected_line = '-6.102 105.423 "Krakatau" color=red image=volcano.png\n'
    assert lines[0] == expected_line, "Volcano marker formatting is incorrect!"
