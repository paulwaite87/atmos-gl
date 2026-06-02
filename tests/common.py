#!/usr/bin/env python3
import os
import sys
import logging
import requests
import pytest
from urllib.parse import urlparse
from PIL import Image

# Append project root to path to ensure clean internal imports
sys.path.insert(
    0, os.path.abspath(str(os.path.join(str(os.path.dirname(__file__)), "..")))
)
from worldmap.lib.config import WorldMapConfig
from worldmap.tasks.common import MapRegion

logger = logging.getLogger("TestUtils")


class MockRegion(MapRegion):
    target_width = 2048
    target_height = 1024
    world_view = True
    region_identifier = "global_test_canvas"
    centre_longitude = 0.0
    centre_latitude = 0.0
    bbox = [-180.0, -90.0, 180.0, 90.0]


class MockMapData:
    def __init__(self, region_override=None):
        self.region = region_override if region_override else MockRegion()


def check_url_accessibility(url, name):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    try:
        response = requests.head(url, timeout=10, allow_redirects=True, headers=headers)
        if response.status_code in [404, 405]:
            response = requests.get(url, timeout=10, stream=True, headers=headers)
            response.close()

        if response.status_code == 403:
            parsed_url = urlparse(url)
            root_url = f"{parsed_url.scheme}://{parsed_url.netloc}/"
            root_resp = requests.head(
                root_url, timeout=10, allow_redirects=True, headers=headers
            )
            if root_resp.status_code < 400:
                return True

        return response.status_code < 400
    except requests.RequestException:
        return False


def assert_url_accessible(url, label):
    """Assertion helper that provides a clear, human-readable error message."""
    assert url, f"{label} 'url' configuration is missing!"

    assert check_url_accessibility(url, label), (
        f"Connectivity Error: The {label} at {url} is currently unreachable. "
        "Please verify your internet connection or the status of the remote server."
    )


def verify_generated_image(
    file_path, expected_width, expected_height, expected_format="PNG"
):
    if not os.path.exists(file_path):
        return False
    try:
        # verify() checks integrity but doesn't load the raster data
        with Image.open(file_path) as img:
            img.verify()

        # Re-open to check attributes (verify() can sometimes leave the file pointer in an unusable state)
        with Image.open(file_path) as img:
            return img.format == expected_format and img.size == (
                expected_width,
                expected_height,
            )
    except Exception as e:
        logger.error(f"Image verification failed for {file_path}: {e}")
        return False


@pytest.fixture
def test_env():
    """Shared fixture that manages paths, configurations, and common canvas contexts."""
    project_root = os.path.abspath(
        str(os.path.join(str(os.path.dirname(__file__)), ".."))
    )
    config_path = os.path.join(project_root, "config", "worldmap.json")

    if not os.path.exists(config_path):
        pytest.fail(f"Config file missing at: {config_path}")

    config = WorldMapConfig(config_path)
    config.setup_for_tests(project_root)

    map_data = MockMapData()
    return {"project_root": project_root, "config": config, "map_data": map_data}
