#!/usr/bin/env python3
import os
import sys
import pytest
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), ".."))))

from worldmap.tasks.shipping import ShippingUpdater
from tests.common import test_env


class MockConfigSection:
    """Duck-types a configparser section to support .get(), .getint(), and .getboolean()"""

    def __init__(self, dictionary):
        self.data = dictionary

    def get(self, key, fallback=None):
        return self.data.get(key, fallback)

    def getint(self, key, fallback=0):
        return int(self.data.get(key, fallback))

    def getboolean(self, key, fallback=False):
        val = self.data.get(key, fallback)
        if val is None:
            return fallback
        return str(val).lower() in ("yes", "true", "t", "1")


class MockShippingUpdater(ShippingUpdater):
    """Subclass of production ShippingUpdater that forces isolated testing output paths."""

    def __init__(self, config, map_data, test_output_path):
        super().__init__(config, map_data)
        self.output_path = test_output_path


def generate_mock_fleet():
    """Generates a list of dictionary payloads matching the psycopg2 RealDictCursor output."""
    return [
        {
            # VALID: Large Tanker, underway. Should appear in output.
            "mmsi": 111111111,
            "name": "TEST TANKER",
            "vessel_type": 80,  # Tanker
            "length": 330,  # VLCC class
            "beam": 60,
            "lat": -18.0,
            "lon": 160.0,
            "cog": 90.0,
            "sog": 12.5,  # Moving > 1 knot
            "nav_status": 0  # Underway using engine
        },
        {
            # INVALID: Fails 'min_length' filter (10m < 50m).
            "mmsi": 222222222,
            "name": "TINY TUGBOAT",
            "vessel_type": 52,  # Tug
            "length": 10,
            "beam": 4,
            "lat": -18.1,
            "lon": 160.1,
            "cog": 0.0,
            "sog": 8.0,
            "nav_status": 0
        },
        {
            # INVALID: Fails 'is_underway' filter (nav_status 1 = anchored, sog 0).
            "mmsi": 333333333,
            "name": "ANCHORED CARGO",
            "vessel_type": 70,  # Cargo
            "length": 200,
            "beam": 30,
            "lat": -18.2,
            "lon": 160.2,
            "cog": 0.0,
            "sog": 0.0,
            "nav_status": 1  # Anchored
        }
    ]


def generate_mock_track():
    """Generates a mock track history for the 'TEST TANKER'."""
    return [
        {"lat": -18.05, "lon": 159.95},
        {"lat": -18.10, "lon": 159.90},
        {"lat": -18.15, "lon": 159.85}
    ]


@pytest.mark.asyncio
async def test_shipping_pipeline(test_env):
    test_output_txt = os.path.join(test_env["project_root"], "data", "test_shipping_output.txt")
    updater = MockShippingUpdater(test_env["config"], test_env["map_data"], test_output_txt)

    # 1. Force the configuration using our MockConfigSection
    updater.settings = MockConfigSection({
        "expiry_days": "7",
        "show_tracks": "True",
        "track_min_distance_km": "2.0",
        "track_max_points": "5",
        "show_ships_underway": "True",
        "show_ship_icons": "Arrows",
        "filter_show_ship_classes": '["Tanker", "Cargo"]',
        "filter_show_names_for_classes": '["Tanker"]',
        "filter_show_ships_by_name": '',
        "filter_ships_minimum_length": "50",
        "label_fontsize": "12",
        "marker_color": "red"
    })

    # 2. Dependency Injection / Mocking
    with patch("worldmap.tasks.shipping.Database") as MockDB, \
            patch.object(updater, "get_cached_rotated_icon", return_value="mock_red_ship_090.png"):
        mock_db_instance = MockDB.return_value
        mock_db_instance.get_fleet.return_value = generate_mock_fleet()
        mock_db_instance.get_ship_track.return_value = generate_mock_track()

        await updater.run()

    # 3. Output Validation
    assert os.path.exists(updater.output_path), "Shipping text output file was not generated."

    with open(updater.output_path, "r") as f:
        output_lines = f.readlines()

    # Verify filtering: Only the "TEST TANKER" should have survived the gauntlet
    marker_lines = [line for line in output_lines if "TEST TANKER" in line]
    assert len(marker_lines) == 1, "Expected exactly 1 valid ship marker."

    main_marker = marker_lines[0]

    # Assert xplanet syntax formatting and logic outcomes
    assert "-18.0 160.0" in main_marker, "Incorrect coordinate placement."
    assert "TEST TANKER Tanker" in main_marker, "Vessel description generation failed."
    assert "fontsize=19" in main_marker, "VLCC size multiplier (12 * 1.6) failed."
    assert "image=mock_red_ship_090.png" in main_marker, "Icon rotation reference missing."

    # Verify rejection criteria (These ships should NOT be in the file)
    assert not any("TINY TUGBOAT" in line for line in output_lines), "Length filter failed."
    assert not any("ANCHORED CARGO" in line for line in output_lines), "Underway filter failed."

    # Verify track generation (Should see plain coordinates written for the track history)
    track_lines = [line for line in output_lines if "color=red" in line and "TEST TANKER" not in line]
    assert len(track_lines) > 0, "Ship tracks were not written to the output."