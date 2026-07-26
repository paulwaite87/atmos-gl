#!/usr/bin/env python3
"""AircraftCollector: Flight Radar's database-backed cache-warming collector (issue
#215, supersedes docs/adr/0009's region-keyed live-poll-and-push architecture).

Unlike RegionManager's WS-route-embedded poll loop (lib/flight_radar.py,
routes/flightradar.py -- still live for now, removed in a later increment once the
REST route lands), this collector is adsb.lol's sole consumer: a persistent async loop,
paced to a fixed requests-per-minute budget, that asks GlobalSampleScheduler which
single cell to sample next -- re-evaluated fresh every tick, so a viewer's viewport
(read from AircraftInterest via AircraftAdapter.get_active_interest) immediately
reshapes priority rather than waiting for a fixed sweep to finish -- and stores whatever
comes back via AircraftAdapter.

Runs as an embedded collector inside CollectorService/data_collector (EMBEDDABLE_COLLECTORS
in collectors/__init__.py), supervised the same way as ShippingCollector/LightningCollector,
not as its own Docker service.
"""
import asyncio
import logging
import time

import aiohttp

from .base import AsyncCollectorBase
from atmos_gl.db.aircraft_adapter import AircraftAdapter
from atmos_gl.lib.flight_radar import (
    ADSB_LOL_BASE,
    COARSE_GRID_DEG,
    GlobalSampleScheduler,
    circle_for_region_key,
    fetch_aircraft_near,
)

logger = logging.getLogger(__name__)


class AircraftCollector(AsyncCollectorBase):
    section = "flightradar_collector"
    # data_collector.datasources.flightradar -- the same shared, maintainable URL list
    # every other collector's datasource lives in, rather than a hardcoded constant.
    datasource_key = "flightradar"

    def __init__(self, config_path: str):
        super().__init__(config_path)
        self.aircraft_adapter = AircraftAdapter()

    def refresh_settings(self) -> None:
        super().refresh_settings()
        # Cached, not resolved fresh per tick, since run()'s loop calls
        # fetch_aircraft_near() far more often than settings actually change.
        # self.base_url is derived from source_url() (the same method the Data Status
        # link uses) rather than a second independent config read, so the two can't
        # silently disagree -- mirrors ShippingCollector.refresh_settings()'s self.url.
        # Falls back to ADSB_LOL_BASE (unlike shipping's AIS URL, which requires an
        # operator-supplied API key) since adsb.lol needs no key and this default is
        # always safe to use even if the datasource entry is ever removed.
        self.base_url = self.source_url() or ADSB_LOL_BASE

    def _tick_interval_seconds(self) -> float:
        """flightradar_collector.requests_per_minute (1-60) is the whole collector's
        adsb.lol request budget -- shared across hotspot and background sampling alike,
        since GlobalSampleScheduler's priority scoring (not a separate sub-budget)
        decides which cell "wins" a given tick."""
        try:
            rpm = float(self.settings.get("requests_per_minute", 6))
        except (TypeError, ValueError):
            rpm = 6.0
        rpm = min(60.0, max(1.0, rpm))
        return 60.0 / rpm

    def _starvation_floor_seconds(self) -> float:
        try:
            minutes = float(self.settings.get("starvation_floor_minutes", 30))
        except (TypeError, ValueError):
            minutes = 30.0
        return max(1.0, minutes) * 60.0

    def _coarse_grid_deg(self) -> float:
        try:
            deg = float(self.settings.get("coarse_grid_deg", COARSE_GRID_DEG))
        except (TypeError, ValueError):
            deg = COARSE_GRID_DEG
        return max(1.0, deg)

    def _interest_max_age_seconds(self) -> float:
        try:
            return float(self.settings.get("interest_max_age_seconds", 30))
        except (TypeError, ValueError):
            return 30.0

    def _report_status(self, status: int) -> None:
        """fetch_aircraft_near's report_status hook -- classifies a raw adsb.lol HTTP
        status into the Data Status page's health icon (issue #215). Deliberately NOT
        called on every 2xx (only on the abnormal cases): read_health_status()'s
        read-time TTL expiry (lib/data_status.py) already reverts the icon to "ok" once
        nothing bad has been reported recently, so there's no need to pay a DB write
        confirming health on every single successful tick."""
        if status == 429:
            self.process_status_adapter.record_health(
                self.section, "collector", "rate_limited", "Rate limited (HTTP 429)"
            )
        elif status >= 400:
            self.process_status_adapter.record_health(
                self.section, "collector", "blocked", f"Blocked (HTTP {status})"
            )

    async def run(self) -> None:
        # Startup heartbeat, same reasoning as ShippingCollector.run()'s: the Data
        # Status UI should show "alive" from the moment this task starts, decaying
        # naturally if nothing follows within heartbeat_period_s.
        self.process_status_adapter.record_process_run(self.section, "collector", success=True)

        # Constructed once, not per-tick: GlobalSampleScheduler carries in-memory
        # per-cell bookkeeping (last-sampled times, empty streaks) that a live config
        # refresh must not wipe. starvation_floor_minutes/coarse_grid_deg are read once
        # here at startup; changing them takes effect on the next restart, same as any
        # other constructor-time-only setting elsewhere in this codebase.
        scheduler = GlobalSampleScheduler(
            starvation_floor_s=self._starvation_floor_seconds(),
            coarse_grid_deg=self._coarse_grid_deg(),
        )

        async with aiohttp.ClientSession() as session:
            while True:
                self.refresh_settings()
                if not self.enabled:
                    logger.debug("AircraftCollector: disabled.")
                    await asyncio.sleep(60)
                    continue

                try:
                    viewports = await asyncio.to_thread(
                        self.aircraft_adapter.get_active_interest,
                        self._interest_max_age_seconds(),
                    )
                    scheduler.set_interest(viewports)

                    now = time.monotonic()
                    cell = scheduler.next_cell(now=now)
                    if cell is not None:
                        grid_deg, ix, iy = cell
                        lat, lon, radius = circle_for_region_key((ix, iy), grid_deg=grid_deg)
                        records = await fetch_aircraft_near(
                            session, lat, lon, radius, base_url=self.base_url,
                            report_status=self._report_status,
                        )
                        scheduler.record_result(cell, records, now=now)

                        if records:
                            stored = await asyncio.to_thread(
                                self.aircraft_adapter.record_sightings, records
                            )
                            logger.debug(
                                f"AircraftCollector: cell {cell} -> {stored} sighting(s)."
                            )

                    self.process_status_adapter.record_process_run(
                        self.section, "collector", success=True
                    )
                except Exception as exc:
                    logger.error(f"AircraftCollector: cycle error: {exc}", exc_info=True)
                    self.process_status_adapter.record_process_run(
                        self.section, "collector", success=False, error=str(exc)
                    )

                await asyncio.sleep(self._tick_interval_seconds())


if __name__ == "__main__":
    AircraftCollector.main()
