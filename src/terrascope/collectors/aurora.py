#!/usr/bin/env python3
"""NOAA SWPC OVATION aurora forecast -> file cache.

A single global grid refreshed upstream about every five minutes, so (like SST and
clouds) it is a file-cache source rather than a fieldstore product: the aurora *updater*
(terrascope.tasks.aurora) renders the texture from the cached grid, and this collector's
only job is to keep that grid fresh. Its endpoint lives in data_collector.datasources
(key "aurora"), and cadence is the usual runs_per_day (default 288, every five minutes).

Collection is UNCONDITIONAL of the layer's `enabled` flag (frontend visibility only).
"""
import logging

from terrascope.collectors.base import CollectorBase
from terrascope.lib.aurora import (
    OVATION_PAGE_URL,
    OVATION_URL,
    aurora_grid_cache_path,
    parse_ovation,
    save_aurora_grid,
)

logger = logging.getLogger(__name__)


class AuroraCollector(CollectorBase):
    section = "aurora"
    channel_key = "aurora"
    datasource_key = "aurora"
    display_label = "NOAA SWPC OVATION"

    def source_url(self) -> str | None:
        """Overridden: the forecast's landing page, not the raw JSON endpoint."""
        return OVATION_PAGE_URL

    def collect(self) -> None:
        url = self.datasource_url(self.datasource_key) or OVATION_URL
        response = self._get(url, timeout=30)
        if response is None:
            # Raised (not just logged) so _drive() records the failure: Data Status then
            # shows the error instead of reporting a stale layer as freshly updated.
            # The cached grid and its times are left exactly as they were.
            raise RuntimeError(f"Aurora: fetch of {url} failed.")
        try:
            probability, observed, forecast = parse_ovation(response.json())
        except (ValueError, KeyError, TypeError) as e:
            raise RuntimeError(f"Aurora: unusable response from {url}: {e}") from e
        save_aurora_grid(aurora_grid_cache_path(self.workdir), probability, observed, forecast)
