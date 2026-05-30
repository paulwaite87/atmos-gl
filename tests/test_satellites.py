#!/usr/bin/env python3
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.satellites import SatelliteUpdater
from tests.common import test_env, assert_url_accessible


class MockSatelliteUpdater(SatelliteUpdater):
    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        # Force config values for predictable testing (Must strictly match lowercase section in worldmap.conf)
        self.config.update_setting("satellites", "enabled", "True")
        self.config.update_setting(
            "satellites", "sat_names", "ISS (ZARYA), CSS (TIANHE)"
        )
        self.config.update_setting(
            "satellites", "url", "https://celestrak.org/NORAD/elements"
        )
        self.config.update_setting("satellites", "degrees_above_horizon", "45")
        self.config.update_setting("satellites", "trail_minutes", "5")

        # Refresh local settings reference
        self.settings = self.config.get_section("satellites")

    def get_base_url(self):
        # Gracefully extract base url similar to production logic
        return (
            self.settings.get("url", "https://celestrak.org/NORAD/elements")
            .strip('"')
            .rstrip("/")
        )


# Dummy TLE payload containing the ISS and Chinese Space Station (CSS)
# Used to verify string extraction (NORAD IDs) and orbital math (Mean Motion -> Altitude)
DUMMY_TLE = """ISS (ZARYA)
1 25544U 98067A   23272.53123843  .00015522  00000-0  28253-3 0  9997
2 25544  51.6415 158.4682 0005781  44.1377 101.4426 15.49842404418042
CSS (TIANHE)
1 48274U 21035A   23273.12345678  .00010000  00000-0  00000-0 0  9999
2 48274  41.4700 120.0000 0001000   0.0000   0.0000 15.60000000123456
"""


def test_satellites_pipeline(test_env):
    updater = MockSatelliteUpdater(test_env["config"], test_env["map_data"])

    # 1. Base URL Reachability Assertion
    base_url = updater.get_base_url()
    assert_url_accessible(base_url, "CelesTrak TLE Server")

    # 2. Cleanup Step: Remove local cache files and output files to force download pipeline
    data_dir = os.path.dirname(updater.output_path) or "data"
    groups = ["stations", "weather", "science", "resource"]

    for group in groups:
        cache_file = os.path.join(data_dir, f"celestrak_{group}.txt")
        if os.path.exists(cache_file):
            os.remove(cache_file)

    if os.path.exists(updater.output_path):
        os.remove(updater.output_path)
    if os.path.exists(f"{updater.output_path}.tle"):
        os.remove(f"{updater.output_path}.tle")

    # 3. Mock HTTP Response
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = DUMMY_TLE
    mock_response.raise_for_status = MagicMock()

    with patch("worldmap.tasks.satellites.requests.get") as mock_get:
        mock_get.return_value = mock_response

        # Execute Pipeline
        updater.run()

        # 4. Request Parameter Verification
        # Because we deleted the local text caches, it should have triggered HTTP requests
        assert mock_get.call_count >= 1, (
            "Updater failed to execute HTTP requests after cache clearance"
        )

    # 5. Output File Verification
    assert os.path.exists(updater.output_path), (
        "Marker file 'sat_file' was not created!"
    )
    assert os.path.exists(f"{updater.output_path}.tle"), (
        "TLE file 'sat_file.tle' was not created!"
    )

    # 6. Orbital Math and Formatting Verification
    with open(updater.output_path, "r") as f_marker:
        marker_content = f_marker.read()

        # NORAD ID extraction checks
        assert "25544" in marker_content, "Failed to extract NORAD ID for ISS"
        assert "48274" in marker_content, "Failed to extract NORAD ID for CSS"

        # Altitude Math checks (Mean Motion -> Altitude conversion)
        # ISS: 15.49842404 mean motion = ~424 km altitude
        assert "[424 km]" in marker_content, (
            "Orbital altitude calculation failed or changed for ISS"
        )
        # CSS: 15.60000000 mean motion = ~394 km altitude
        assert "[394 km]" in marker_content, (
            "Orbital altitude calculation failed or changed for CSS"
        )

        # Xplanet configuration attribute checks
        assert "image=none" in marker_content, "Missing 'image=none' declaration"
        assert "altcirc=" in marker_content, (
            "Missing 'altcirc' (Degrees above horizon) declaration"
        )
        assert "trail={orbit" in marker_content, (
            "Missing 'trail' orbit projection declaration"
        )

    with open(f"{updater.output_path}.tle", "r") as f_tle:
        tle_content = f_tle.read()

        # Validate that exact orbital geometry was exported faithfully
        assert "ISS (ZARYA)" in tle_content, "TLE export missing ISS header"
        assert "1 25544U" in tle_content, "TLE export missing ISS Line 1"
        assert "2 25544 " in tle_content, "TLE export missing ISS Line 2"
