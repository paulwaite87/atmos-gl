#!/usr/bin/env python3
import os
import sys
import logging

# Need Pillow for the transparency and compositing
from PIL import Image

# Internal library import
from worldmap.lib.config import WorldMapConfig
from .common import Updater, MapData, COMPOSITE_SECTIONS

logger = logging.getLogger(__name__)


class CompositeUpdater(Updater):
    """
    Joins the enabled weather layers (SST, Clouds, Precipitation, Isobars, Wind) into a single map.
    Layers are applied dynamically bottom-to-top based on configuration.
    """

    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Composite", map_data)

    def run(self):
        """Combines the enabled weather layers onto the map background."""
        self.exit_if_disabled()

        logger.debug("Starting composite updater")
        layers = []
        for section in COMPOSITE_SECTIONS:
            if self.config.section_enabled(section):
                section_image_path = self.get_output_path_if_exists(section)
                if section_image_path:
                    layers.append((section, section_image_path))

        if not layers:
            logger.debug("No composite layers enabled. Skipping.")
            return

        # Validate files exist
        for label, path in layers:
            if not os.path.exists(path):
                logger.error(f"Source file missing ({label}): {path}")
                sys.exit(1)

        # Case: Compositing process
        try:
            logger.debug(f"Compositing layers: {[layer[0] for layer in layers]}...")

            # Use target dimensions from the MapData object to create a standardized canvas.
            # This prevents clipping when a single layer's aspect ratio differs from the background.
            target_size = (self.target_width, self.target_height)
            bg_img = Image.new("RGBA", target_size, (0, 0, 0, 0))

            for label, path in layers:
                with Image.open(path) as overlay_img:
                    overlay_img = overlay_img.convert("RGBA")

                    # Resize to match the global project dimensions using high-quality resampling
                    if overlay_img.size != target_size:
                        overlay_img = overlay_img.resize(
                            target_size, Image.Resampling.LANCZOS
                        )

                    # Paste layer using its own alpha channel as the mask
                    bg_img.paste(overlay_img, (0, 0), mask=overlay_img)

            # Save final standardized output
            bg_img.save(self.output_path, "PNG")
            logger.debug(f"Successfully created composite: {self.output_path}")

        except Exception as e:
            logger.error(f"Unexpected error during PIL composite: {e}")
            sys.exit(1)
