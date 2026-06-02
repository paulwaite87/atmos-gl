#!/usr/bin/env python3
import os
import sys
import logging
import requests
from datetime import datetime as dt, timedelta, timezone
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.quakes import QuakeUpdater
from tests.common import test_env, assert_url_accessible


class MockConfigSection:
    """Duck-types a configparser section to support .get() and .get()"""

    def __init__(self, dictionary):
        self.data = dictionary

    def get(self, key):
        return self.data.get(key, None)


class MockQuakeUpdater(QuakeUpdater):
    """Subclass of production QuakeUpdater that isolates execution for testing."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)

    def exit_if_disabled(self):
        """Bypass the enabled/disabled check during unit testing."""
        pass


def test_quake_url_inaccessible(test_env, caplog):
    """Test that the QuakeUpdater cleanly handles and logs an inaccessible URL."""
    updater = MockQuakeUpdater(test_env["config"], test_env["map_data"])

    url = "http://example.com/nonexistent"
    updater.settings = MockConfigSection({"url": url})

    # 1. Mock the network request to fail deterministically
    with patch("worldmap.tasks.quakes.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404 Client Error"
        )
        mock_get.return_value = mock_resp

        # 2. Capture the log output and run
        with caplog.at_level(logging.ERROR):
            updater.run()

    # 3. Assert that the expected error was captured by the logging framework
    assert any(
        "Error fetching quakes: 404 Client Error" in record.message
        for record in caplog.records
    )


def test_quake_pipeline(test_env):
    updater = MockQuakeUpdater(test_env["config"], test_env["map_data"])

    # 1. Force the configuration to guarantee specific execution paths
    # We explicitly test the magnitude float parsing and fallback defaults.
    updater.settings = MockConfigSection(
        {
            "url": updater.settings.get("url"),
            "marker_color": "red",
            "marker_symbol_new": "eq_new.png",
            "marker_symbol_old": "eq_old.png",
            "label_fontsize": "14",
            "min_mag": "5.0",
            "recent_activity_hours": "3",
            "expiry_hours": "12",
        }
    )

    # 2. Base URL Reachability Assertion
    # Verifies the live USGS feed endpoint is actually online.
    base_url = updater.settings.get("url", "").strip('"').rstrip("/")
    assert_url_accessible(base_url, "USGS Earthquake Feed")

    # 3. Dependency Injection / Mocking
    # Provide a mock CSV payload with three quakes: Mag 6.2, Mag 4.2 (Should filter), and Mag 5.1
    now_utc = dt.now(timezone.utc)
    quake_time1 = (now_utc - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S")  # qualifies as recent
    quake_time2 = (now_utc - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%S")  # qualifies as old
    quake_time3 = (now_utc - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%S")  # qualifies as old

    mock_csv_data = (
        "time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type\n"
        f"{quake_time1}Z,-18.5,160.0,15,6.2,mww,10,20,0.5,0.8,us,us1000abcd,{quake_time1}Z,Test Strong Quake,earthquake\n"
        f"{quake_time2}Z,-19.0,161.0,10,4.2,mb,5,30,0.6,0.9,us,us1000abce,{quake_time2}Z,Test Weak Quake,earthquake\n"
        f"{quake_time3}Z,-20.0,162.0,22,5.1,mww,8,25,0.7,0.7,us,us1000abcf,{quake_time3}Z,Test Borderline Quake,earthquake\n"
    )

    with patch("worldmap.tasks.quakes.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_csv_data
        mock_get.return_value = mock_resp

        # Execute the pipeline with the mocked USGS payload
        updater.run()

    # 4. Output Logic Validations
    assert os.path.exists(updater.output_path), (
        "Quake text output file was not generated."
    )

    with open(updater.output_path, "r") as f:
        output_lines = f.readlines()

    # Verify Pandas Filtering Logic: The 4.2 magnitude quake should be dropped by min_mag=5.0
    assert len(output_lines) == 2, (
        f"Expected exactly 2 quakes to pass the filter, but got {len(output_lines)}."
    )

    # Verify XPlanet Marker Syntax Formatting
    first_quake = output_lines[0]
    assert "-18.5 160.0" in first_quake, "Incorrect coordinate placement formatting."
    assert '"M6.2 15km"' in first_quake, "Incorrect label combination formatting."
    assert "color=red" in first_quake, "Marker color assignment failed."
    assert "fontsize=14" in first_quake, "Font size assignment failed."
    assert "image=eq_new.png" in first_quake, "Marker symbol (new) assignment failed."

    second_quake = output_lines[1]
    assert "-20.0 162.0" in second_quake, "Incorrect coordinate placement formatting."
    assert '"M5.1 22km"' in second_quake, "Incorrect label combination formatting."
    assert "image=eq_old.png" in second_quake, "Marker symbol (old) assignment failed."

    # Final safety check to ensure the weak quake is nowhere in the file
    assert not any("M4.2" in line for line in output_lines), (
        "Quake below min_mag threshold was leaked into output!"
    )

    # Clean up dummy test file
    if os.path.exists(updater.output_path):
        os.remove(updater.output_path)
