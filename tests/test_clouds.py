#!/usr/bin/env python3
import os
import sys
import pytest
import io
from unittest.mock import patch, MagicMock
from PIL import Image

# Append project root to path to ensure clean internal imports
sys.path.insert(0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), ".."))))

from worldmap.tasks.clouds import CloudUpdater
from tests.common import test_env, assert_url_accessible, verify_generated_image


class MockCloudUpdater(CloudUpdater):
    """Subclass of production NasaCloudUpdater for test consistency."""

    def __init__(self, config, map_data):
        super().__init__(config, map_data)


def generate_dummy_jpeg_bytes(width, height):
    """Generates valid JPEG binary data in memory for the mock HTTP response."""
    img = Image.new("RGB", (width, height), color=(135, 206, 235))  # Sky blue
    byte_io = io.BytesIO()
    img.save(byte_io, format="JPEG")
    return byte_io.getvalue()


def test_clouds_pipeline(test_env):
    updater = MockCloudUpdater(test_env["config"], test_env["map_data"])

    # 1. Base URL Reachability Assertion
    base_url = updater.settings.get("url", "").strip('"').rstrip("/")
    assert_url_accessible(base_url, "NASA GIBS WMS Server")

    # 2. Mocking the HTTP response
    dummy_jpeg_data = generate_dummy_jpeg_bytes(
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height
    )

    mock_response = MagicMock()
    mock_response.read.return_value = dummy_jpeg_data
    mock_response.__enter__.return_value = mock_response

    # Force the config flag to bypass the time-based caching logic
    updater.config.has_changed = True

    # 3. Pipeline Execution via Context Injection
    with patch("worldmap.tasks.clouds.urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value = mock_response
        updater.run()

        # 4. Request Parameter Verification
        # Extract the actual Request object passed to urlopen
        mock_urlopen.assert_called_once()
        requested_url = mock_urlopen.call_args[0][0].full_url

        # Verify critical WMS payload constraints are present in the URL
        assert "LAYERS=VIIRS_SNPP_CorrectedReflectance_TrueColor" in requested_url
        assert "FORMAT=image/jpeg" in requested_url
        assert f"WIDTH={updater.target_width}" in requested_url
        assert f"HEIGHT={updater.target_height}" in requested_url
        assert "TIME=" in requested_url

    # 5. Structural Image Layout Verification
    assert verify_generated_image(
        updater.output_path,
        test_env["map_data"].region.target_width,
        test_env["map_data"].region.target_height,
        expected_format="JPEG"
    ), "Clouds JPEG failed verification!"