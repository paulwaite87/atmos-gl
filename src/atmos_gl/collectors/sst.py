#!/usr/bin/env python3
"""OISST sea-surface-temperature source -> file cache.

Unlike the GFS/RTOFS field collectors, SST is a single yearly netCDF (one daily field,
not per-forecast-hour), so it lives as a cache file under {workdir}/data rather than as a
stored fieldstore product. The sst *updater* (atmos_gl.tasks.sst) renders PNGs from this
cache; this collector's only job is to keep the netCDF fresh.

Migrated out of the monolithic DataCollector as the first slice of the collector-per-file
refactor. Fits the plain CollectorBase contract with no extra scaffolding because it
neither resolves a model baseline nor writes to the fieldstore — it just downloads a file.

Freshness is two-layered, matching the event feeds:
  * is_stale()  — orchestrator cadence, from runs_per_day (sst config: 2/day).
  * collect()   — owns the real skip decision via remote_is_newer() (HEAD Last-Modified),
                  so a due-but-unchanged remote costs one HEAD and no download.

Collection is UNCONDITIONAL of the layer's `enabled` flag: `enabled` is a frontend
visibility control only; the cache must be warm so the layer renders the moment it's
toggled on.
"""
import os
import logging

from atmos_gl.collectors.base import CollectorBase
from atmos_gl.lib.oisst import build_oisst_url, oisst_cache_path, remote_is_newer
from atmos_gl.lib.gfs import download_whole

logger = logging.getLogger(__name__)


class SstCollector(CollectorBase):
    section = "sst"

    def collect(self) -> None:
        """Download the yearly OISST netCDF (for the mode the sst layer renders) into the
        shared file cache the sst updater reads, refreshing only when the remote is newer.
        """
        url_base = self.settings.get("url", "").rstrip("/")
        if not url_base:
            logger.warning("SST: no url configured; skipping.")
            return

        mode = self.settings.get("mode", "absolute")
        url = build_oisst_url(url_base, mode)
        dest = oisst_cache_path(self.workdir, mode)
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        if not remote_is_newer(url, dest):
            logger.debug(f"SST: cache up to date ({os.path.basename(dest)}).")
            return

        try:
            logger.info(f"SST: downloading {url}")
            data = download_whole(url, timeout=300)
        except Exception as e:
            logger.error(f"SST: download failed: {e}")
            return

        tmp = f"{dest}.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, dest)
        logger.info(
            f"SST: wrote {len(data) / 1e6:.1f} MB -> {os.path.basename(dest)}"
        )
