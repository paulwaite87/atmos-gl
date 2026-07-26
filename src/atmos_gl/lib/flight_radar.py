#!/usr/bin/env python3
"""Flight Radar's data acquisition (issue #203/#215). Originally region-keyed
backend-proxy-and-push (docs/adr/0009, superseded by docs/adr/0010): pure geometry
helpers (circle_for_region_key, fetch_aircraft_near) plus a stateful RegionManager
driving a WebSocket-push route. RegionManager and its WS route (routes/flightradar.py)
were removed once AircraftCollector (collectors/aircraft.py) took over as adsb.lol's
sole consumer -- GlobalSampleScheduler below is what actually schedules sampling now.
The geometry helpers survive unchanged; AircraftCollector reuses circle_for_region_key
and fetch_aircraft_near exactly as RegionManager's poll loop did.
"""
import logging
import math

import aiohttp

logger = logging.getLogger("atmos_gl.lib.flight_radar")

ADSB_LOL_BASE = "https://api.adsb.lol/v2"

# Grid cell size in degrees for the fine/hotspot tier -- also GlobalSampleScheduler's
# FINE_GRID_DEG, so an active viewer's hot cell lines up with what the frontend itself
# considers "the area in view." Not tuned against real adsb.lol traffic yet; ~5deg
# (~550km at the equator) is a starting guess in the same ballpark as the hot circle's
# own radius, left for empirical tuning during rollout like every other numeric
# constant in this feature.
GRID_DEG = 5.0

# adsb.lol query radius, nautical miles. adsb.lol never confirmed a max radius during
# research; ADSBExchange-family APIs typically cap around 250nm. 200nm is a starting
# guess -- large enough to reasonably cover a GRID_DEG cell from its center (a 5deg
# cell's corner is ~215nm from center), tuned empirically once live.
CIRCLE_RADIUS_NM = 200.0


def _cell(lon: float, lat: float, grid_deg: float) -> tuple[int, int]:
    return (math.floor(lon / grid_deg), math.floor(lat / grid_deg))


def circle_for_region_key(
    region_key: tuple[int, int], *, grid_deg: float = GRID_DEG, radius_nm: float = CIRCLE_RADIUS_NM,
) -> tuple[float, float, float]:
    """A region key (grid cell) -> the (lat, lon, radius_nm) circle queried for it,
    centered on the cell. One circle per region key -- doesn't perfectly cover every
    corner of the cell at every grid_deg/radius_nm combination; an accepted, tunable
    imprecision (see CIRCLE_RADIUS_NM's docstring)."""
    lon_idx, lat_idx = region_key
    lon = (lon_idx + 0.5) * grid_deg
    lat = (lat_idx + 0.5) * grid_deg
    return lat, lon, radius_nm


async def fetch_aircraft_near(
    session: aiohttp.ClientSession, lat: float, lon: float, radius_nm: float,
    *, base_url: str = ADSB_LOL_BASE, timeout: float = 10.0, report_status=None,
) -> list[dict] | None:
    """One adsb.lol point+radius query -> its `ac` (aircraft) list, or None on any
    failure (timeout, non-200 -- adsb.lol's free tier 429s far more readily than its
    documented behaviour suggests, malformed response). None is deliberately distinct
    from [] : a failed request must never crash the poll loop, but it also must never
    be reported to callers as "confirmed zero aircraft here" -- see
    GlobalSampleScheduler.record_result(), whose whole reason for accepting None is
    this distinction.

    base_url defaults to ADSB_LOL_BASE but is normally overridden by the caller with
    the configured data_collector.datasources.flightradar value (AircraftCollector) --
    same "URL lives in the shared datasources dict, not hardcoded" convention every
    other collector follows.

    report_status, if given, is called with the raw HTTP status code once a response
    is actually received (never called if the request raised before completing --
    a timeout/connection error, as opposed to a real rejection). Purely a side-channel
    for Data Status health reporting (see AircraftCollector._report_status()),
    independent of this function's own None-vs-[] success/failure contract -- a single
    rate-limited request shouldn't be conflated with "the fetch failed"."""
    url = f"{base_url}/lat/{lat}/lon/{lon}/dist/{radius_nm}"
    try:
        async with session.get(url, timeout=timeout) as resp:
            if report_status:
                report_status(resp.status)
            if resp.status != 200:
                logger.debug(f"adsb.lol {url} returned {resp.status}")
                return None
            data = await resp.json()
            return data.get("ac", []) or []
    except Exception as exc:
        logger.debug(f"adsb.lol fetch failed for {url}: {exc}")
        return None


# --- Global cache-warming sweep (issue #215): GlobalSampleScheduler is what
# AircraftCollector actually uses to decide what to sample each tick -- see that
# class's docstring for how it generalizes the region-keyed due/longest-waiting-first
# shape the removed RegionManager used to implement. ---

# The fine grid shares GRID_DEG (the viewport hot-cell resolution) so an active viewer's
# hot cell lines up exactly with what the frontend itself considers "the area in view."
FINE_GRID_DEG = GRID_DEG

# The background sweep's own, much coarser grid -- required arithmetically, not just for
# convenience: a 30-minute starvation floor (STARVATION_FLOOR_S) at a 6/minute request
# budget can cover at most 180 cells globally (30 * 6). GRID_DEG's own 2,592 cells
# (72 x 36) would need ~14x that budget, or a ~7-hour floor, to keep the same guarantee --
# so the background tier tiles the globe far more coarsely instead. 30deg -> 12 x 6 = 72
# cells, comfortably under budget with headroom for hot-cell traffic interleaved.
COARSE_GRID_DEG = 30.0

HOT_CADENCE_S = 10.0
BACKGROUND_CADENCE_S = 60.0
STARVATION_FLOOR_S = 1800.0

# A background cell needs this many consecutive empty results before its effective
# cadence starts being stretched out (see GlobalSampleScheduler._effective_cadence) --
# below this it's still treated as "unknown", not "reliably empty".
EMPTY_STREAK_THRESHOLD = 3
# Cap on how far a persistently-empty cell's effective cadence can be stretched, so it's
# deprioritized, never fully starved outright (STARVATION_FLOOR_S still forces a
# recheck regardless).
EMPTY_STREAK_MAX_PENALTY = 10.0

# Cap on how many fine-grid cells a single viewport can claim as "hot", so an extremely
# zoomed-out viewport can't blow the request budget by claiming hundreds of cells at
# HOT_CADENCE_S. The cells actually kept are always the ones nearest the viewport
# center -- same nearest-first-under-a-cap shape the old (removed) RegionManager-era
# viewport_to_region_keys used for its gentle tier.
MAX_HOT_CELLS_PER_VIEWPORT = 12


class GlobalSampleScheduler:
    """Pure, now-driven priority queue for AircraftCollector's cache-warming sweep
    (issue #215). Generalizes a due/longest-waiting-first scheduling shape (this
    module's own predecessor, the now-removed RegionManager, used the same idea for
    "regions a WebSocket viewport subscribed to") to the whole globe: a fine grid
    (FINE_GRID_DEG) covers whichever cells currently have an active viewer (per
    set_interest()), sampled at HOT_CADENCE_S; a fixed coarse grid (COARSE_GRID_DEG)
    covers everywhere else, sampled at BACKGROUND_CADENCE_S but adaptively slowed down
    for cells that keep coming back empty. A hard STARVATION_FLOOR_S ceiling overrides
    both, so no part of the globe goes unsampled indefinitely.

    Not asyncio-aware itself: owns no tasks, does no I/O, and doesn't read viewer
    interest from the database itself -- the caller (AircraftCollector) reads
    AircraftAdapter.get_active_interest() and hands the result to set_interest() each
    cycle. Takes an explicit `now` on every call, a tick-driven-state-machine shape
    (mirroring CollectorBase.is_stale()), so it's testable with controlled timestamps."""

    def __init__(
        self,
        *,
        fine_grid_deg: float = FINE_GRID_DEG,
        coarse_grid_deg: float = COARSE_GRID_DEG,
        hot_cadence_s: float = HOT_CADENCE_S,
        background_cadence_s: float = BACKGROUND_CADENCE_S,
        starvation_floor_s: float = STARVATION_FLOOR_S,
    ):
        self._fine_grid_deg = fine_grid_deg
        self._coarse_grid_deg = coarse_grid_deg
        self._hot_cadence_s = hot_cadence_s
        self._background_cadence_s = background_cadence_s
        self._starvation_floor_s = starvation_floor_s

        # cell key: (grid_deg, ix, iy). Absent from _last_sampled_at => never sampled.
        self._last_sampled_at: dict[tuple, float] = {}
        self._empty_streak: dict[tuple, int] = {}
        # Fine cells backed by at least one active interest viewport as of the most
        # recent set_interest() call -- recomputed fresh every tick, never accumulated.
        self._hot_cells: set[tuple] = set()

    def set_interest(self, viewports: list[tuple[float, float, float, float]]) -> None:
        """Recomputes which fine-grid cells are 'hot' this tick, from the caller's
        fresh read of currently-active viewer interest (west, south, east, north).
        Every fine cell the viewport actually touches becomes hot (capped at
        MAX_HOT_CELLS_PER_VIEWPORT, nearest-to-center first) -- not just the cell at
        its center: "the hotspot" means the whole visible area, with the coarse
        background sweep picking up just outside it, matching this feature's original
        design intent (issue #215).

        Callers are expected to have already filtered out stale/expired interest rows
        (see AircraftAdapter.get_active_interest's max_age_s) -- this method doesn't
        read a clock itself, it just takes whatever's handed to it. Doesn't handle a
        viewport crossing the antimeridian (west > east) -- a known simplification for
        v1, inherited from the pre-issue-#215 viewport_to_region_keys this replaces."""
        hot = set()
        for viewport in viewports:
            hot.update(self._cells_for_viewport(viewport))
        self._hot_cells = hot

    def _cells_for_viewport(self, viewport: tuple[float, float, float, float]) -> list[tuple]:
        """Every fine-grid cell a viewport bbox touches, nearest-to-center first and
        capped at MAX_HOT_CELLS_PER_VIEWPORT -- shared by set_interest() (which only
        needs the resulting set) and hotspot_progress() (which needs this exact same
        per-viewport list to report "N of M cells queried" for just this viewport)."""
        west, south, east, north = viewport
        center_lon, center_lat = (west + east) / 2.0, (south + north) / 2.0
        center = _cell(center_lon, center_lat, self._fine_grid_deg)

        lon_lo, lon_hi = _cell(west, 0.0, self._fine_grid_deg)[0], _cell(east, 0.0, self._fine_grid_deg)[0]
        lat_lo, lat_hi = _cell(0.0, south, self._fine_grid_deg)[1], _cell(0.0, north, self._fine_grid_deg)[1]

        candidates = [
            (lx, ly)
            for lx in range(lon_lo, lon_hi + 1)
            for ly in range(lat_lo, lat_hi + 1)
        ]
        candidates.sort(key=lambda c: (c[0] - center[0]) ** 2 + (c[1] - center[1]) ** 2)
        return [
            (self._fine_grid_deg, ix, iy)
            for ix, iy in candidates[:MAX_HOT_CELLS_PER_VIEWPORT]
        ]

    def hotspot_progress(self, viewports: list[tuple[float, float, float, float]]) -> dict:
        """{"queried": n, "total": m} across every fine-grid cell the given viewports
        touch (deduplicated -- two overlapping viewports don't double-count a shared
        cell), "queried" meaning ever sampled at all (not just since becoming hot --
        a cell the background sweep already warmed before anyone looked at it is
        genuinely already populated, not a bug). Callers should pass the SAME
        viewports list just given to set_interest() -- this doesn't read
        self._hot_cells directly since that's a flat union with no per-call viewport
        boundary to report progress against. {"queried": 0, "total": 0} when
        `viewports` is empty (no active viewer to report progress for)."""
        cells: set[tuple] = set()
        for viewport in viewports:
            cells.update(self._cells_for_viewport(viewport))
        total = len(cells)
        queried = sum(1 for c in cells if self._last_sampled_at.get(c) is not None)
        return {"queried": queried, "total": total}

    def _all_coarse_cells(self) -> list[tuple]:
        n_lon = int(360 / self._coarse_grid_deg)
        n_lat = int(180 / self._coarse_grid_deg)
        lon0 = math.floor(-180.0 / self._coarse_grid_deg)
        lat0 = math.floor(-90.0 / self._coarse_grid_deg)
        return [
            (self._coarse_grid_deg, lon0 + i, lat0 + j)
            for i in range(n_lon)
            for j in range(n_lat)
        ]

    def _elapsed(self, cell: tuple, *, now: float) -> float:
        last = self._last_sampled_at.get(cell)
        return float("inf") if last is None else now - last

    def _effective_cadence(self, cell: tuple) -> float:
        """Hot cells are never adaptively deprioritized -- an active viewer's own cell
        should always use hot_cadence_s. Background cells that keep coming back empty
        get a slower effective cadence (capped at EMPTY_STREAK_MAX_PENALTY x), freeing
        budget for cells with actual traffic; the starvation floor still eventually
        forces a recheck regardless of streak."""
        if cell in self._hot_cells:
            return self._hot_cadence_s
        streak = self._empty_streak.get(cell, 0)
        if streak < EMPTY_STREAK_THRESHOLD:
            return self._background_cadence_s
        penalty = min(EMPTY_STREAK_MAX_PENALTY, 1 + (streak - EMPTY_STREAK_THRESHOLD + 1))
        return self._background_cadence_s * penalty

    def next_cell(self, *, now: float) -> tuple | None:
        """The next cell to sample this tick: any cell that has breached the
        starvation floor wins outright (oldest-first, never-sampled first); otherwise
        the most-overdue cell against its own effective cadence -- never-sampled cells
        sort first, longest-waiting-first among the rest. None if nothing is due at all."""
        candidates = set(self._hot_cells) | set(self._all_coarse_cells())
        if not candidates:
            return None

        floored = [c for c in candidates if self._elapsed(c, now=now) >= self._starvation_floor_s]
        if floored:
            return min(floored, key=lambda c: self._last_sampled_at.get(c, float("-inf")))

        due = [c for c in candidates if self._elapsed(c, now=now) >= self._effective_cadence(c)]
        if not due:
            return None
        return min(due, key=lambda c: self._last_sampled_at.get(c, float("-inf")))

    def record_result(self, cell: tuple, records: list[dict] | None, *, now: float) -> None:
        """records=None (failed fetch) still advances last_sampled_at (so the cell
        backs off at its normal cadence rather than being retried immediately) but
        does NOT count toward the empty streak -- a rejected request isn't evidence a
        cell has no traffic, just that the request failed."""
        self._last_sampled_at[cell] = now
        if records is None:
            return
        if records:
            self._empty_streak[cell] = 0
        else:
            self._empty_streak[cell] = self._empty_streak.get(cell, 0) + 1
