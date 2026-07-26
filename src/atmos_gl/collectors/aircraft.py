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
from atmos_gl.lib.data_status import (
    build_status,
    estimate_next_update,
    read_health_status,
    read_process_status,
)
from atmos_gl.lib.flight_radar import (
    ADSB_LOL_BASE,
    COARSE_GRID_DEG,
    GlobalSampleScheduler,
    circle_for_region_key,
    fetch_aircraft_near,
)

logger = logging.getLogger(__name__)

# process_status name for the Flight Radar hotspot-coverage progress bar (issue #215)
# -- deliberately distinct from both `section` ("flightradar_collector", the
# collector's OWN row in the Data Status Collectors panel) and the "flightradar"
# config section (the frontend layer), since this is a third, separate row rendered
# in the Layers panel instead (see routes/status.py).
HOTSPOT_STATUS_NAME = "flightradar_hotspot"


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
        decides which cell "wins" a given tick.

        Default 12 (not the original 6): one fetch/tick means a full round-robin of
        MAX_HOT_CELLS_PER_VIEWPORT (12) hot cells takes (12 cells * tick interval) in
        the worst case -- at 6rpm/10s-ticks that's 120s per cell, far past the
        frontend's freeze threshold (flightradar.js's MAX_EXTRAPOLATION_S) almost all
        of the time. 12rpm/5s-ticks brings that worst case down to 60s, matching
        MAX_EXTRAPOLATION_S's own bump to 60s -- see that constant's comment for the
        other half of this same tradeoff."""
        try:
            rpm = float(self.settings.get("requests_per_minute", 12))
        except (TypeError, ValueError):
            rpm = 12.0
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

    def data_status(self) -> dict:
        """Coverage-based override of AsyncCollectorBase.data_status(): percent is the
        fraction of the globe (GlobalSampleScheduler's fixed background grid, NOT just
        whatever a viewer happens to be looking at) currently within its starvation-
        floor freshness window -- "how many of the global regions have up-to-date
        data right now", not a liveness heartbeat. The inherited default
        (freshness_data_status()) would read 100% for as long as run()'s loop kept
        ticking every few seconds, regardless of how much of the globe actually had
        fresh data -- a heartbeat, not a coverage measure.

        progress_current/progress_total are written every tick by run() via
        record_progress(self.section, ...) -- the same decoupled progress columns
        HOTSPOT_STATUS_NAME's row uses, just on this row instead. record_progress(),
        record_health(), and record_process_run() each touch only their own column
        set, so all three coexist on this one process_status row without clobbering
        each other (see test_process_status_adapter_real_vs_fake.py's coverage of
        exactly this). last_updated/status/health still come from the same row, read
        the normal way."""
        last_updated, last_error, status = read_process_status(
            self.process_status_adapter, self.section
        )
        row = self.process_status_adapter.get_process_status(self.section) or {}
        total = row.get("progress_total") or 0
        current = row.get("progress_current") or 0
        percent = 100.0 * current / total if total else 0.0
        detail = last_error or (
            f"{current}/{total} global region(s) up to date" if total else None
        )
        return build_status(
            name=self.section,
            kind="collector",
            percent=percent,
            last_updated=last_updated,
            next_update=estimate_next_update(last_updated, self.heartbeat_period_s, self.enabled),
            enabled=self.enabled,
            detail=detail,
            status=status,
            health=read_health_status(self.process_status_adapter, self.section),
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

                    # Hotspot-coverage progress bar (issue #215): "N of M currently-
                    # prioritized cells have data" for whichever viewport(s) are
                    # active right now -- {"queried": 0, "total": 0} when none are,
                    # which routes/status.py reads as "no active viewport" rather
                    # than leaving a stale prior reading on screen.
                    progress = scheduler.hotspot_progress(viewports)
                    self.process_status_adapter.record_progress(
                        HOTSPOT_STATUS_NAME, "layer",
                        progress["queried"], progress["total"],
                    )

                    now = time.monotonic()

                    # Global coverage (see data_status()): unlike the hotspot progress
                    # bar above, this is unconditional of any active viewport -- it's a
                    # property of the scheduler's whole background-grid state, written
                    # onto this collector's OWN row every tick regardless of who's
                    # looking at the map right now.
                    coverage = scheduler.global_coverage(now=now)
                    self.process_status_adapter.record_progress(
                        self.section, "collector",
                        coverage["fresh"], coverage["total"],
                    )

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
