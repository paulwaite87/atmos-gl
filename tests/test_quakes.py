#!/usr/bin/env python3
import os
import sys
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), ".."))))

from worldmap.tasks.quakes import QuakeUpdater
from tests.common import test_env, check_url_accessibility


class MockConfigSection:
    """Duck-types a configparser section to support .get() and .getfloat()"""
    def __init__(self, dictionary):
        self.data = dictionary

    def get(self, key, fallback=None):
        return self.data.get(key, fallback)

    def getfloat(self, key, fallback=0.0):
        return float(self.data.get(key, fallback))


class MockQuakeUpdater(QuakeUpdater):
    """Subclass of production QuakeUpdater that isolates execution for testing."""
    def __init__(self, config, map_data, test_output_path):
        super().__init__(config, map_data)
        self.output_path = test_output_path

    def exit_if_disabled(self):
        """Bypass the enabled/disabled check during unit testing."""
        pass


def test_quake_pipeline(test_env):
    test_output_txt = os.path.join(test_env["project_root"], "data", "test_quakes_output.txt")
    updater = MockQuakeUpdater(test_env["config"], test_env["map_data"], test_output_txt)

    # 1. Force the configuration to guarantee specific execution paths
    # We explicitly test the magnitude float parsing and fallback defaults.
    updater.settings = MockConfigSection({
        "url": "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_month.csv",
        "marker_color": "red",
        "marker_symbol": "quake.png",
        "label_fontsize": "14",
        "min_mag": "5.0"
    })

    # 2. Base URL Reachability Assertion
    # Verifies the live USGS feed endpoint is actually online.
    live_url = updater.settings.get("url")
    assert live_url, "Quakes 'url' configuration is missing!"
    assert check_url_accessibility(live_url.strip(), "USGS Earthquake Feed")

    # 3. Dependency Injection / Mocking
    # Provide a mock CSV payload with three quakes: Mag 6.2, Mag 4.2 (Should filter), and Mag 5.1
    mock_csv_data = (
        "time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type\n"
        "2026-05-21T00:00:00Z,-18.5,160.0,15,6.2,mww,10,20,0.5,0.8,us,us1000abcd,2026-05-21T01:00:00Z,Test Strong Quake,earthquake\n"
        "2026-05-21T00:10:00Z,-19.0,161.0,10,4.2,mb,5,30,0.6,0.9,us,us1000abce,2026-05-21T01:10:00Z,Test Weak Quake,earthquake\n"
        "2026-05-21T00:20:00Z,-20.0,162.0,22,5.1,mww,8,25,0.7,0.7,us,us1000abcf,2026-05-21T01:20:00Z,Test Borderline Quake,earthquake\n"
    )

    with patch("worldmap.tasks.quakes.requests.get") as mock_get:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = mock_csv_data
        mock_get.return_value = mock_resp

        # Execute the pipeline with the mocked USGS payload
        updater.run()

    # 4. Output Logic Validations
    assert os.path.exists(updater.output_path), "Quake text output file was not generated."

    with open(updater.output_path, "r") as f:
        output_lines = f.readlines()

    # Verify Pandas Filtering Logic: The 4.2 magnitude quake should be dropped by min_mag=5.0
    assert len(output_lines) == 2, f"Expected exactly 2 quakes to pass the filter, but got {len(output_lines)}."

    # Verify XPlanet Marker Syntax Formatting
    first_quake = output_lines[0]
    assert "-18.5 160.0" in first_quake, "Incorrect coordinate placement formatting."
    assert '"M6.2 15km"' in first_quake, "Incorrect label combination formatting."
    assert "color=red" in first_quake, "Marker color assignment failed."
    assert "fontsize=14" in first_quake, "Font size assignment failed."
    assert "image=quake.png" in first_quake, "Marker symbol assignment failed."

    second_quake = output_lines[1]
    assert "-20.0 162.0" in second_quake, "Incorrect coordinate placement formatting."
    assert '"M5.1 22km"' in second_quake, "Incorrect label combination formatting."

    # Final safety check to ensure the weak quake is nowhere in the file
    assert not any("M4.2" in line for line in output_lines), "Quake below min_mag threshold was leaked into output!"

    # Clean up dummy test file
    if os.path.exists(updater.output_path):
        os.remove(updater.output_path)