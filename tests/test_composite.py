#!/usr/bin/env python3
import os
import sys
from pathlib import Path
from PIL import Image
from unittest.mock import patch, MagicMock

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)

from worldmap.tasks.composite import CompositeUpdater
from tests.common import test_env, verify_generated_image


class MockCompositeUpdater(CompositeUpdater):
    """Subclass of production CompositeUpdater for test consistency."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)
        # Force a dedicated testing output path
        self.output_path = os.path.join(self.workdir, "data", "test_composite.png")


def test_composite_pipeline(test_env):
    updater = MockCompositeUpdater(test_env["config"], test_env["map_data"])

    # 1. Force the configuration to enable exactly the layers we want to test
    updater.clouds_enabled = True
    updater.sst_enabled = True
    updater.config.update_setting("sst", "enabled", "True")
    updater.config.update_setting("clouds", "enabled", "True")

    # Configure the cloud transparency LUT parameters
    updater.config.update_setting("clouds","threshold", "10")
    updater.config.update_setting("clouds","gamma", "1.5")

    # 2. Generate dummy input layers on disk
    os.makedirs(os.path.join(updater.workdir, "data", "regions"), exist_ok=True)
    sst_path = os.path.join(updater.workdir, "data", "test_sst.png")

    # NEW: Match the exact regional layout calculated by composite.py for the cloud base image
    cloud_filename = f"clouds_{updater.map_data.region.region_identifier}_{updater.target_width}x{updater.target_height}.jpg"
    clouds_path = os.path.join(updater.workdir, "data", "regions", cloud_filename)

    # Sea Surface Temp: Solid red with 50% opacity (RGBA)
    Image.new(
        "RGBA", (updater.target_width, updater.target_height), (255, 0, 0, 128)
    ).save(sst_path)
    # Clouds: Solid mid-gray to test the _apply_cloud_transparency gamma/threshold calculations
    # Saved as RGB since production reads a downloaded JPEG via Image.open()
    Image.new(
        "RGB", (updater.target_width, updater.target_height), (128, 128, 128)
    ).save(clouds_path)

    # 3. Patching dependencies
    # Intercept the path lookups to return our dummy SST file
    def mock_get_output_path(section):
        if section == "sst":
            return sst_path
        return None

    updater.get_output_path_if_exists = MagicMock(side_effect=mock_get_output_path)

    # 4. Pipeline Execution via Context Injection
    # Restrict the composite engine to only iterate over the two layers we just mocked
    test_composite_sequence = ["sst", "clouds"]

    with patch("worldmap.tasks.composite.COMPOSITE_SECTIONS", test_composite_sequence):
        updater.run()

    # 5. File Generation & Format Verification
    assert os.path.exists(updater.output_path), "Composite PNG was not generated!"

    # NEW: Assert check against the modified regional transparency filename template
    regional_cloud_map = os.path.join(
        updater.workdir,
        "data",
        "regions",
        f"clouds_transparent_{updater.map_data.region.region_identifier}_{updater.target_width}x{updater.target_height}.png",
    )
    assert os.path.exists(regional_cloud_map), (
        "Regional transparency cloud map was not generated!"
    )

    # Verify structural integrity of the final composited canvas
    assert verify_generated_image(
        updater.output_path,
        updater.target_width,
        updater.target_height,
        expected_format="PNG",
    ), "Final Composite PNG failed structural verification!"
