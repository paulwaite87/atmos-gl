#!/usr/bin/env python3
import os
import pandas as pd
from worldmap.tasks.storms import StormUpdater
from tests.common import test_env, check_url_accessibility, verify_generated_image


class MockStormUpdater(StormUpdater):
    def __init__(self, config, map_data, test_output_path):
        super().__init__(config, map_data)
        self.output_path = test_output_path

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
    updater = MockStormUpdater(
        test_env["config"],
        test_env["map_data"],
        os.path.join(test_env["project_root"], "data", "test_storms_output.png")
    )

    # 1. URL Asset Safety Assertions
    ibtracs_base = updater.settings.get("ibtracs_url")
    jtwc_url = updater.settings.get("jtwc_url")
    nhc_url = updater.settings.get("nhc_url")

    assert ibtracs_base, "ibtracs_url is unconfigured!"
    assert check_url_accessibility(ibtracs_base.strip(), "IBTrACS Hub")
    assert check_url_accessibility(jtwc_url.strip(), "JTWC Feed") if jtwc_url else True
    assert check_url_accessibility(nhc_url.strip(), "NHC Feed") if nhc_url else True

    # 2. BeautifulSoup Layout Extraction Validation
    active_csv_url = updater.get_active_csv_url()
    assert active_csv_url, "Could not retrieve active CSV url"
    if active_csv_url != "No ACTIVE storms":
        assert check_url_accessibility(active_csv_url, "Target Active CSV Data File")

    # 3. Graphics Processing Engine Assertion
    updater.generate_and_render_mock(-18.5, 160.0)
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height
    )