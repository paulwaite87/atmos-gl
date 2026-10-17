#!/usr/bin/env python3
"""NASA GIBS Black Marble night-lights image source -> file cache.

A file-cache source like clouds: one global image plus a sidecar recording its date.
The night-lights *updater* reads both and renders the transparent overlay.

Unlike clouds the date is not "today minus N": GIBS's gap-filled product runs a couple
of days behind and has holes, so the newest date is read from the layer's capabilities,
with the previous date as the fallback when the newest fails or comes back empty.

Its endpoint lives in data_collector.datasources (key "night_lights").
"""

import os
import time
import logging
import urllib.request

from terrascope.collectors.base import CollectorBase
from terrascope.lib.gibs import (
    build_night_lights_url,
    layer_dates,
    night_lights_cache_path,
    write_night_lights_date,
    NIGHT_LIGHTS_LAYER,
)

logger = logging.getLogger(__name__)

_DEFAULT_GEOMETRY = "2048x1024"
_UA = {"User-Agent": "Terrascope-NightLights-Fetcher/1.0"}


class NightLightsCollector(CollectorBase):
    section = "night_lights"
    channel_key = "night_lights"
    datasource_key = "night_lights"

    def _open(self, url):
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.read()

    def collect(self) -> None:
        base_url = self.datasource_url("night_lights")
        if not base_url:
            logger.warning("Night lights: no url configured; skipping.")
            return

        dest = night_lights_cache_path(self.workdir)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        expiry_hours = float(self.settings.get("expiry_hours", 12))
        if os.path.exists(dest):
            age_h = (time.time() - os.path.getmtime(dest)) / 3600.0
            if age_h < expiry_hours:
                logger.debug(f"Night lights: cache fresh ({age_h:.1f}h); skipping.")
                return

        try:
            caps = self._open(
                f"{base_url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetCapabilities"
            )
            dates = layer_dates(caps, NIGHT_LIGHTS_LAYER)
        except Exception as e:
            logger.error(f"Night lights: capabilities fetch failed: {e}")
            return

        geom = self.config.get_setting("common", "target_geometry", _DEFAULT_GEOMETRY)
        try:
            width, height = (int(x) for x in geom.lower().split("x"))
        except Exception:
            width, height = 2048, 1024

        for day in dates[:2]:
            try:
                data = self._open(build_night_lights_url(base_url, width, height, day))
            except Exception as e:
                logger.warning(f"Night lights: {day} failed: {e}")
                continue
            if data:
                break
            logger.warning(f"Night lights: {day} came back empty")
        else:
            logger.error("Night lights: no usable image; keeping the previous one.")
            return

        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        write_night_lights_date(self.workdir, day)
        logger.info(f"Night lights: wrote {len(data) / 1e6:.1f} MB for {day}")
