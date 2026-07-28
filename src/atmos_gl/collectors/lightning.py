#!/usr/bin/env python3
"""OpenWeather lightning strike data -> database.

Long-running async collector: every sleep_interval minutes (the "Sleep interval"
slider, 5-30, default 10 -- see _sleep_interval_seconds()), fetches a rate-limited
budget's worth of 50km-radius grid points via aiohttp. Runs as its own Docker service
because the blocking GFS downloads in DataCollector.collect_once() would starve its
event loop (follow-on: asyncio.to_thread consolidation into data_collector).

Request volume/pacing redesign: the naive "scan every region's full grid every cycle"
approach used ~88,500 requests per full sweep across the 12 seeded regions -- OpenWeather's
lightning plan (60 calls/min, 1,000,000/month) allows less than one full sweep per DAY
once averaged over a month (the monthly quota, not the per-minute cap, turns out to be
the binding constraint -- see MAX_CALLS_PER_MONTH's comment). So this no longer scans
"every region, every cycle": each cycle pulls a fixed, budget-sized slice from one
flattened global grid-point queue (build_grid()), split between points near the
frontend's last-reported viewport (ViewportAdapter -- see its docstring for why this
has to be DB-backed, not in-memory) and a persistent round-robin cursor advancing
through the rest, so background regions still get covered over time even though they
can't all be scanned every cycle at this quota.

Moved from src/atmos_gl/lightning_collector.py to src/atmos_gl/collectors/lightning.py
to live under the shared collectors umbrella.
"""
import heapq
import os
import time
import logging
import asyncio
import aiohttp
from datetime import datetime, timedelta, timezone

from .base import AsyncCollectorBase
from atmos_gl.db.lightning_adapter import LightningAdapter
from atmos_gl.db.region_adapter import RegionAdapter
from atmos_gl.db.viewport_adapter import ViewportAdapter

logger = logging.getLogger(__name__)

# OpenWeather's documented plan limits for this API key, throttled to 80% of each so
# the collector never rides right up against either cap (headroom for other traffic on
# the same key, and against edge-of-limit 429s).
OPENWEATHER_CALLS_PER_MINUTE = 60
OPENWEATHER_CALLS_PER_MONTH = 1_000_000
_HEADROOM = 0.8
MAX_CALLS_PER_MINUTE = OPENWEATHER_CALLS_PER_MINUTE * _HEADROOM  # 48
MAX_CALLS_PER_MONTH = OPENWEATHER_CALLS_PER_MONTH * _HEADROOM  # 800,000
SECONDS_PER_MONTH = 30 * 24 * 3600

# Each request already queries a 50km radius; spacing grid centers at radius*sqrt(2)
# (~70km, vs. the old 50km) keeps full coverage (no gaps between adjacent circles)
# while cutting the heavy overlap a 1:1 radius:spacing grid produces -- roughly halves
# point count with no loss of coverage.
GRID_RADIUS_KM = 50
GRID_STEP_DEG = (GRID_RADIUS_KM * 2**0.5) / 111.0  # ~0.637 deg

# Fraction of each cycle's budget reserved for the points nearest the last-reported
# viewport (if known and fresh); the rest advances the round-robin cursor.
VIEWPORT_BUDGET_FRACTION = 0.5
# A viewport reading older than this is treated as unknown (e.g. nobody has the map
# open, or the frontend's been idle/auto-rotating) -- the full budget then goes to the
# round-robin cursor instead of stale-chasing a viewport nobody's looking at anymore.
VIEWPORT_STALE_AFTER_S = 3600.0


class RateLimiter:
    """Token bucket capping requests to calls_per_minute, refilling continuously, with
    a shared exponential backoff triggered by 429s.

    Unlike a per-request retry (the old code's `await asyncio.sleep(1)` on the single
    failed point), note_429() pauses ALL subsequent acquire() calls -- a 429 means the
    whole collector is going too fast, not just that one point, so backing off only
    that one request would leave the rest hammering an already-limited endpoint at the
    same pace.
    """

    def __init__(self, calls_per_minute: float):
        self.rate = calls_per_minute / 60.0  # tokens/sec
        self.capacity = calls_per_minute
        self.tokens = calls_per_minute
        self._updated = time.monotonic()
        self._backoff_until = 0.0
        self._consecutive_429s = 0

    async def acquire(self) -> None:
        while True:
            now = time.monotonic()
            if now < self._backoff_until:
                await asyncio.sleep(self._backoff_until - now)
                continue
            elapsed = now - self._updated
            self._updated = now
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            if self.tokens >= 1:
                self.tokens -= 1
                return
            await asyncio.sleep((1 - self.tokens) / self.rate)

    def note_429(self) -> None:
        self._consecutive_429s += 1
        backoff_s = min(60.0, 2**self._consecutive_429s)
        self._backoff_until = time.monotonic() + backoff_s
        logger.warning(f"LightningCollector: rate limited; backing off {backoff_s:.0f}s.")

    def note_success(self) -> None:
        self._consecutive_429s = 0


def build_grid(regions, step_deg: float = GRID_STEP_DEG) -> list[tuple[float, float]]:
    """Flattens every region's bounding box into one ordered list of (lat, lon) points
    at step_deg spacing. Regions are sorted by label so the list's order is stable
    across calls given the same region definitions -- the round-robin cursor in
    select_points_for_cycle() depends on that stability to make forward progress."""
    points = []
    for region in sorted(regions, key=lambda r: r["label"]):
        lon_min, lon_max = sorted([region["lon_min"], region["lon_max"]])
        lat_min, lat_max = sorted([region["lat_min"], region["lat_max"]])
        lat = lat_min + step_deg / 2
        while lat <= lat_max:
            lon = lon_min + step_deg / 2
            while lon <= lon_max:
                points.append((lat, lon))
                lon += step_deg
            lat += step_deg
    return points


def select_points_for_cycle(
    all_points: list[tuple[float, float]],
    cursor: int,
    budget: int,
    viewport: tuple[float, float] | None = None,
    viewport_budget_fraction: float = VIEWPORT_BUDGET_FRACTION,
) -> tuple[list[tuple[float, float]], int]:
    """Picks up to `budget` points for one scan cycle.

    Up to viewport_budget_fraction*budget of the points nearest `viewport` (a
    (lat, lon) tuple) are always included first, if a viewport is given -- this keeps
    whatever's currently on-screen close to real-time regardless of overall coverage.
    The remaining budget advances a persistent round-robin cursor through all_points
    (wrapping at the end), skipping any point already selected for viewport-priority,
    so background regions keep rotating through coverage over time.

    Returns (selected_points, next_cursor).
    """
    if not all_points or budget <= 0:
        return [], cursor

    selected: list[tuple[float, float]] = []
    selected_set: set[tuple[float, float]] = set()

    if viewport is not None:
        vlat, vlon = viewport
        near_budget = int(budget * viewport_budget_fraction)
        nearest = heapq.nsmallest(
            near_budget, all_points, key=lambda p: (p[0] - vlat) ** 2 + (p[1] - vlon) ** 2
        )
        for p in nearest:
            selected.append(p)
            selected_set.add(p)

    remaining = budget - len(selected)
    n = len(all_points)
    idx = cursor % n
    steps = 0
    while remaining > 0 and steps < n:
        p = all_points[idx]
        if p not in selected_set:
            selected.append(p)
            selected_set.add(p)
            remaining -= 1
        idx = (idx + 1) % n
        steps += 1

    return selected, idx


class LightningCollector(AsyncCollectorBase):
    section = "lightning_collector"
    datasource_key = "lightning"

    def __init__(self, config_path: str):
        super().__init__(config_path)
        self.lightning_adapter = LightningAdapter()
        self.region_adapter = RegionAdapter()
        self.viewport_adapter = ViewportAdapter()
        self.rate_limiter = RateLimiter(MAX_CALLS_PER_MINUTE)
        self._cursor = 0

    @property
    def heartbeat_period_s(self) -> float:
        """Expected gap between heartbeats: one scan-then-sleep cycle. sleep_interval is
        now configurable (5-30 min), so a fixed allowance no longer fits every setting --
        this scales with it (+300s buffer for scan time, matching the old fixed 900s
        value's margin over the previous hardcoded 600s sleep)."""
        return self._sleep_interval_seconds() + 300.0

    def _sleep_interval_seconds(self) -> float:
        """lightning_collector.sleep_interval (the "Sleep interval" slider, 5-30) is
        stored/edited in MINUTES; run()'s pause between scans needs seconds."""
        try:
            minutes = int(self.settings.get("sleep_interval", 10))
        except (TypeError, ValueError):
            minutes = 10
        minutes = min(30, max(5, minutes))
        return minutes * 60.0

    def _points_budget_for_cycle(self, sleep_interval_s: float) -> int:
        """How many grid points this cycle may fetch, given the current sleep_interval.
        Budgeted off whichever of the two OpenWeather caps is more restrictive once
        averaged per-second -- the monthly quota (800,000/month with headroom, this
        deployment sees far fewer than 60 calls/min sustained), not the instantaneous
        per-minute cap, turns out to be the binding constraint in practice."""
        per_minute_rate = MAX_CALLS_PER_MINUTE / 60.0
        per_month_rate = MAX_CALLS_PER_MONTH / SECONDS_PER_MONTH
        sustainable_rate = min(per_minute_rate, per_month_rate)
        return max(1, int(sleep_interval_s * sustainable_rate))

    def refresh_settings(self) -> None:
        super().refresh_settings()
        # Cached (not resolved fresh at each fetch) since fetch_and_store() is called
        # per 50km grid point -- potentially hundreds of times per scan. self.url is
        # derived from source_url() (the same method the Data Status link uses) rather
        # than a second independent config read, so the two can't silently disagree.
        self.url = self.source_url() or ""
        # API key: config file first, then environment variable.
        self.api_key = (
            self.settings.get("api_key")
            or os.environ.get("OPENWEATHER_API_KEY")
        )
        if not self.api_key:
            logger.error(
                "LightningCollector: no API key found in config or OPENWEATHER_API_KEY env var."
            )

    def _current_viewport(self) -> tuple[float, float] | None:
        """The frontend's last-reported map center, or None if never reported or too
        stale (see VIEWPORT_STALE_AFTER_S) to treat as "what's currently on screen"."""
        row = self.viewport_adapter.get_viewport()
        if row is None or row.get("updated_at") is None:
            return None
        age_s = (datetime.now(timezone.utc) - row["updated_at"]).total_seconds()
        if age_s > VIEWPORT_STALE_AFTER_S:
            return None
        return (row["lat"], row["lon"])

    async def fetch_and_store(self, session, lat, lon, start_iso, end_iso):
        if not self.api_key:
            return 0
        await self.rate_limiter.acquire()
        params = {
            "lat": f"{lat:.4f}",
            "lon": f"{lon:.4f}",
            "radius": GRID_RADIUS_KM,
            "start_date": start_iso,
            "end_date": end_iso,
            "apikey": self.api_key,
        }
        try:
            async with session.get(self.url, params=params, timeout=12) as resp:
                if resp.status == 200:
                    self.rate_limiter.note_success()
                    data = await resp.json()
                    strikes = data.get("lightnings", [])
                    for s in strikes:
                        self.lightning_adapter.update_lightning_strike(
                            strike_id=s["id"],
                            lat=s["lat"],
                            lon=s["lon"],
                            quality=s["quality"],
                            timestamp_iso=s["datetime"],
                        )
                    return len(strikes)
                elif resp.status == 429:
                    self.rate_limiter.note_429()
        except Exception as exc:
            logger.debug(f"LightningCollector: block {lat},{lon} failed: {exc}")
        return 0

    async def scan_points(self, session, points, start_iso, end_iso) -> int:
        """Fetches `points` in small concurrent batches (latency-hiding only -- actual
        throughput pacing comes from each fetch_and_store() call's rate_limiter.acquire(),
        not a fixed inter-batch sleep)."""
        total_strikes = 0
        for i in range(0, len(points), 5):
            batch = points[i:i + 5]
            results = await asyncio.gather(
                *[self.fetch_and_store(session, lat, lon, start_iso, end_iso) for lat, lon in batch]
            )
            total_strikes += sum(results)
        return total_strikes

    async def run(self) -> None:
        # Startup heartbeat: the Data Status UI should show "the collector is alive" the
        # moment this task starts, not leave a blank "never" until the first full scan
        # completes. Percent decays from here if the first scan itself hangs, so this
        # can't mask a real problem.
        self.process_status_adapter.record_process_run(self.section, "collector", success=True)

        while True:
            self.refresh_settings()

            if self.enabled:
                logger.info("LightningCollector: starting scan cycle.")
                now = datetime.now(timezone.utc)
                start_iso = (now - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")
                end_iso = now.strftime("%Y-%m-%dT%H:%M:%SZ")

                try:
                    sleep_interval_s = self._sleep_interval_seconds()
                    regions = self.region_adapter.get_all_regions()
                    all_points = build_grid(regions)
                    budget = self._points_budget_for_cycle(sleep_interval_s)
                    viewport = self._current_viewport()

                    selected, self._cursor = select_points_for_cycle(
                        all_points, self._cursor, budget, viewport=viewport
                    )

                    async with aiohttp.ClientSession() as session:
                        strikes = await self.scan_points(session, selected, start_iso, end_iso)

                    expiry_hours = self.settings.get("expiry_hours", 2)
                    pruned = self.lightning_adapter.prune_lightning(expiry_hours=expiry_hours)
                    if pruned:
                        logger.debug(f"LightningCollector: pruned {pruned} expired strikes.")
                    logger.info(
                        f"LightningCollector: scan complete. {len(selected)} points queried, "
                        f"{strikes} strikes recorded."
                    )
                    self.process_status_adapter.record_process_run(self.section, "collector", success=True)
                except Exception as exc:
                    logger.error(f"LightningCollector: scan error: {exc}")
                    self.process_status_adapter.record_process_run(
                        self.section, "collector", success=False, error=str(exc)
                    )
            else:
                logger.debug("LightningCollector: disabled.")

            await asyncio.sleep(self._sleep_interval_seconds())


if __name__ == "__main__":
    LightningCollector.main()

def main():
    LightningCollector.main()
