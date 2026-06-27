#!/usr/bin/env python3
import os
import logging
from PIL import Image

# Internal library import
from worldmap.lib.config import WorldMapConfig
from worldmap.lib.gibs import clouds_cache_path
from .common import Updater, MapData

logger = logging.getLogger(__name__)


class CloudUpdater(Updater):
    def __init__(self, config: WorldMapConfig, map_data: MapData):
        super().__init__(config, "Clouds", map_data)
        # The data_collector now owns the GIBS download; we read the single global cache
        # it maintains and turn it into a transparent overlay.
        self.cache_output_path = clouds_cache_path(self.workdir)

    def save_cache_as_transparent(self):
        threshold = self.settings.get("threshold", 0)
        gamma = self.settings.get("gamma", 1.0)
        with Image.open(self.cache_output_path) as raw_clouds_image:
            cloud_mask = raw_clouds_image.convert("L")
            lut = [
                int(pow(i / 255.0, 1.0 / gamma) * 255.0) if i >= threshold else 0
                for i in range(256)
            ]
            cloud_mask = cloud_mask.point(lut)
            transparent_clouds_image = Image.new(
                "RGBA", raw_clouds_image.size, (0, 0, 0, 0)
            )
            white_clouds = Image.new("RGBA", raw_clouds_image.size, (255, 255, 255, 255))
            transparent_clouds_image.paste(white_clouds, (0, 0), mask=cloud_mask)
        logger.debug(f"Saving transparent cloud map in {self.output_path}")
        transparent_clouds_image.save(self.output_path, "PNG")

    def run(self):
        """Render the transparent cloud overlay from the collector-maintained cache.
        No network: the data_collector fetches the GIBS image. (Re)process only when the
        cache is newer than our output, so we don't repaint an unchanged image."""
        if not os.path.exists(self.cache_output_path):
            logger.info(
                f"Clouds: cache {os.path.basename(self.cache_output_path)} not present "
                "yet (data collector hasn't fetched it); skipping."
            )
            return

        out = self.output_path
        if (
            out
            and os.path.exists(out)
            and os.path.getmtime(out) >= os.path.getmtime(self.cache_output_path)
        ):
            logger.debug("Clouds: output already up to date with cache; skipping.")
            return

        self.save_cache_as_transparent()
