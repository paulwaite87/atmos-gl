#!/usr/bin/env python3
"""NASA GIBS global cloud image source -> file cache.

Like SST, this is a file-cache source (a single global RGB image), not a fieldstore
product: the clouds *updater* reads the cached image and composites it. This collector
just keeps the cache warm.

Its endpoint lives in data_collector.datasources (key "clouds"), same as every other
source -- see CollectorBase.datasource_url().

Freshness is two-layered:
  * is_stale()  — orchestrator cadence; period_s comes from CollectorBase's default
                  runs_per_day-derived formula, same as quakes/volcanoes/storms/etc.
  * collect()   — owns the real skip decision via the cache-age vs expiry_hours check,
                  so a due-but-still-fresh cache costs an mtime stat and no download.

Collection is UNCONDITIONAL of the layer's `enabled` flag (frontend visibility only).
"""
import os
import time
import logging
import urllib.request
from datetime import datetime, timezone, timedelta

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.gibs import build_clouds_url, clouds_cache_path

logger = logging.getLogger(__name__)

_DEFAULT_GEOMETRY = "2048x1024"


class CloudsCollector(CollectorBase):
    section = "clouds"
    channel_key = "clouds"
    datasource_key = "clouds"

    def collect(self) -> None:
        """Fetch the global GIBS cloud image into the shared cache the clouds layer reads,
        refreshing only when the cache is missing or older than expiry_hours. The date is
        the most recent complete day (now - offset_days) so VIIRS swaths are complete."""
        base_url = self.datasource_url("clouds")
        if not base_url:
            logger.warning("Clouds: no url configured; skipping.")
            return

        dest = clouds_cache_path(self.workdir)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # Refresh only if the cache is missing or older than expiry_hours.
        expiry_hours = float(self.settings.get("expiry_hours", 3))
        if os.path.exists(dest):
            age_h = (time.time() - os.path.getmtime(dest)) / 3600.0
            if age_h < expiry_hours:
                logger.debug(f"Clouds: cache fresh ({age_h:.1f}h); skipping.")
                return

        # Dimensions from the global target geometry; date = now - offset_days.
        geom = self.config.get_setting("common", "target_geometry", _DEFAULT_GEOMETRY)
        try:
            width, height = (int(x) for x in geom.lower().split("x"))
        except Exception:
            width, height = 2048, 1024
        offset_days = int(self.settings.get("offset_days", 1))
        time_param = (
            datetime.now(timezone.utc) - timedelta(days=offset_days)
        ).strftime("%Y-%m-%d")
        layers = self.settings.get(
            "layers", "VIIRS_SNPP_CorrectedReflectance_TrueColor"
        )

        url = base_url if "matteason" in base_url else build_clouds_url(base_url, width, height, time_param, layers=layers)
        try:
            logger.info(f"Clouds: fetching GIBS {time_param} ({width}x{height})")
            req = urllib.request.Request(
                url, headers={"User-Agent": "AtmosGL-Cloud-Fetcher/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                data = response.read()
        except Exception as e:
            logger.error(f"Clouds: fetch failed: {e}")
            return

        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        logger.info(
            f"Clouds: wrote {len(data) / 1e6:.1f} MB -> {os.path.basename(dest)}"
        )
