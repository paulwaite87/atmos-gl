#!/usr/bin/env python3
"""NOAA OISST sea-ice concentration -> file cache.

Like SST this is one yearly netCDF (a daily field per day, not per-forecast-hour), so it
lives as a cache file under {workdir}/data rather than a fieldstore product: the sea ice
*updater* (terrascope.tasks.sea_ice) renders the latest day from it, and this collector's
only job is to keep that file fresh. Its endpoint lives in data_collector.datasources
(key "sea_ice"); cadence is the usual runs_per_day (default 2).

Freshness is two-layered, as for SST: is_stale() is the orchestrator cadence, and
collect() owns the real skip decision via remote_is_newer() (HEAD Last-Modified), so a
due-but-unchanged remote costs one HEAD and no download.

Collection is UNCONDITIONAL of the layer's `enabled` flag (frontend visibility only).
"""
import logging
import os
from datetime import datetime, timezone

import requests

from terrascope.collectors.base import CollectorBase
from terrascope.lib.gfs import download_whole
from terrascope.lib.oisst import remote_is_newer
from terrascope.lib.sea_ice import OISST_BASE_URL, OISST_PAGE_URL, sea_ice_cache_path, sea_ice_url

logger = logging.getLogger(__name__)


class SeaIceCollector(CollectorBase):
    section = "sea_ice"
    channel_key = "sea_ice"
    datasource_key = "sea_ice"
    display_label = "NOAA OISST Sea Ice"

    def source_url(self) -> str | None:
        """Overridden: the dataset's landing page, not the raw netCDF."""
        return OISST_PAGE_URL

    def collect(self) -> None:
        base = self.datasource_url(self.datasource_key) or OISST_BASE_URL
        year = datetime.now(timezone.utc).year
        dest = sea_ice_cache_path(self.workdir)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # The new year's file only exists once NOAA has published its first day, so in
        # early January a missing (404) file means "use last year's", not an error. Only a
        # genuine 404 falls back: a transient failure must never swap in last year's ice.
        for candidate in (year, year - 1):
            url = sea_ice_url(base, candidate)
            if not remote_is_newer(url, dest):
                logger.debug(f"Sea ice: cache up to date ({os.path.basename(dest)}).")
                return
            logger.info(f"Sea ice: downloading {url}")
            try:
                data = download_whole(url, timeout=300)
            except requests.HTTPError as e:
                if candidate == year and e.response is not None and e.response.status_code == 404:
                    logger.info(f"Sea ice: {url} not published yet; trying {year - 1}.")
                    continue
                raise RuntimeError(f"Sea ice: download of {url} failed: {e}") from e
            except Exception as e:
                # Raised (not just logged) so _drive() records the failure; the cached
                # file is left exactly as it was.
                raise RuntimeError(f"Sea ice: download of {url} failed: {e}") from e
            tmp = f"{dest}.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, dest)
            logger.info(f"Sea ice: wrote {len(data) / 1e6:.1f} MB -> {os.path.basename(dest)}")
            return
