#!/usr/bin/env python3
import pandas as pd
from worldmap.tasks.storms import StormUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockStormUpdater(StormUpdater):
    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        self.set_output_path()

    def generate_and_render_mock(self, lat, lon):
        mock_data = []
        sid, name = "TEST_9999", "CYBER_STORM"
        for hours_ago in range(48, 0, -6):
            mock_data.append(
                {"SID": sid, "NAME": name, "LAT": lat + (hours_ago * 0.08), "LON": lon - (hours_ago * 0.12),
                 "TYPE": "PAST", "TAU": 0})
        mock_data.append({"SID": sid, "NAME": name, "LAT": lat, "LON": lon, "TYPE": "CURRENT", "TAU": 0})
        for tau in [12, 24, 36, 48, 72, 96, 120]:
            mock_data.append(
                {"SID": sid, "NAME": name, "LAT": lat - (tau * 0.10), "LON": lon + (tau * 0.05) + ((tau ** 2) * 0.0005),
                 "TYPE": "FORECAST", "TAU": tau})

        self.plot_storms(pd.DataFrame(mock_data))


def test_storm_pipeline(test_env):
    updater = MockStormUpdater(test_env["config"], test_env["map_data"])

    # 1. URL Asset Safety Assertions
    jtwc_url = updater.settings.get("jtwc_url")
    nhc_url = updater.settings.get("nhc_url")

    assert jtwc_url, "jtwc_url is unconfigured!"
    assert nhc_url, "nhc_url is unconfigured!"

    # Derive NHC Best Track URL identically to runtime logic
    nhc_btk_url = nhc_url.strip().replace("fst", "btk")

    assert_url_accessible(jtwc_url.strip(), "JTWC Forecast Directory Hub")
    assert_url_accessible(nhc_url.strip(), "NHC Forecast Directory Hub")
    assert_url_accessible(nhc_btk_url, "NHC Best-Track Directory Hub")

    # 2. BeautifulSoup Directory Layout Extraction Validation
    # Verifies the parser reads the remote HTML indexing structures without crash
    jtwc_files = updater._get_file_list(jtwc_url.strip())
    assert isinstance(jtwc_files, list), "Failed to extract directory listing array from JTWC"

    nhc_files = updater._get_file_list(nhc_btk_url)
    assert isinstance(nhc_files, list), "Failed to extract directory listing array from NHC Best Track"

    # 3. Graphics Processing Engine Assertion
    updater.generate_and_render_mock(-18.5, 160.0)
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height
    )