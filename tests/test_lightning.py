#!/usr/bin/env python3
import os
import sys
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.lightning import LightningUpdater
from tests.common import test_env


class MockLightningUpdater(LightningUpdater):
    """Subclass of production LightningUpdater for test consistency."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        # config.setup_for_tests() automatically modifies the output paths
        # so super().set_output_path() correctly assigns a 'test_' prefixed path.


@pytest.mark.asyncio
async def test_lightning_pipeline(test_env):
    updater = MockLightningUpdater(test_env["config"], test_env["map_data"])

    # Force specific configuration parameters for the test
    updater.strike_recent_minutes = 15
    updater.strike_keep_minutes = 60
    updater.strike_expiry_minutes = 2 * 60

    now = datetime.now(timezone.utc)

    # Create mock strikes at different ages to trigger all three icon conditions
    mock_strikes = [
        # < 15 mins old ; Should map to bolt_white.png
        {"lat": -40.1, "lon": 170.1, "timestamp": now - timedelta(minutes=5)},
        # 15-60 mins old ; Should map to bolt_yellow.png
        {"lat": -40.2, "lon": 170.2, "timestamp": now - timedelta(minutes=30)},
        # >60 mins old and < 2h ; Should map to bolt_red.png
        {"lat": -40.3, "lon": 170.3, "timestamp": now - timedelta(minutes=90)},
    ]

    # Patch the database so we don't attempt a real SQLite/Postgres connection
    with patch("worldmap.tasks.lightning.Database") as MockDB:
        mock_db_instance = MockDB.return_value
        mock_db_instance.get_lightning_in_region.return_value = mock_strikes

        # Execute the async pipeline
        await updater.run()

        # Verify the database query was executed with the exact bounding box and age limits
        lon_min, lat_min, lon_max, lat_max = updater.map_region_bbox
        mock_db_instance.get_lightning_in_region.assert_called_once_with(
            lon_min, lat_min, lon_max, lat_max, expiry_minutes=120
        )

    # File Generation & Format Verification
    assert os.path.exists(updater.output_path), (
        "Lightning output text file was not generated!"
    )

    with open(updater.output_path, "r") as f:
        lines = f.readlines()

    # We expect exactly 3 lines, one for each mock strike
    assert len(lines) == 3, f"Expected 3 lightning strikes, but got {len(lines)}."

    # Verify the formatting and aging logic is strictly applied
    assert lines[0].strip() == "-40.1 170.1 image=bolt_white.png", (
        "White bolt logic failed!"
    )
    assert lines[1].strip() == "-40.2 170.2 image=bolt_yellow.png", (
        "Yellow bolt logic failed!"
    )
    assert lines[2].strip() == "-40.3 170.3 image=bolt_red.png", (
        "Red bolt logic failed!"
    )
