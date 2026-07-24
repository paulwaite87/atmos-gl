#!/usr/bin/env python3
"""Flight Radar's region-keyed backend-proxy-and-push data acquisition (issue #203,
docs/adr/0009 -- supersedes the stateless per-viewport pass-through in docs/adr/0007).

Pure geometry helpers live here (viewport_to_region_keys, circle_for_region_key); the
stateful RegionManager -- one poll loop per region key, shared by every WebSocket
connection subscribed to it -- lives here too, since it's a single cohesive concern for
one feature (mirrors how lib/rtofs.py bundles URL-building and baseline-resolution for
one data source rather than splitting them).
"""
import logging
import math

import aiohttp

logger = logging.getLogger("atmos_gl.lib.flight_radar")

ADSB_LOL_BASE = "https://api.adsb.lol/v2"

# Grid cell size in degrees. A coarse bucket so nearby/overlapping viewports converge
# onto the same region key and share one poll loop -- not tuned against real adsb.lol
# traffic yet; ~5deg (~550km at the equator) is a starting guess in the same ballpark
# as the hot circle's own radius (see circles_covering_region), left for empirical
# tuning during rollout like every other numeric constant in this feature.
GRID_DEG = 5.0

# Cap on how many gentle-tier region keys a single viewport subscribes to, so an
# extremely wide (zoomed-out) viewport can't fan out into dozens of region
# subscriptions. The cells actually kept are always the ones nearest the hot cell.
MAX_GENTLE_KEYS = 8

# adsb.lol query radius, nautical miles. adsb.lol never confirmed a max radius during
# research; ADSBExchange-family APIs typically cap around 250nm. 200nm is a starting
# guess -- large enough to reasonably cover a GRID_DEG cell from its center (a 5deg
# cell's corner is ~215nm from center), tuned empirically once live.
CIRCLE_RADIUS_NM = 200.0


def _cell(lon: float, lat: float, grid_deg: float) -> tuple[int, int]:
    return (math.floor(lon / grid_deg), math.floor(lat / grid_deg))


def viewport_to_region_keys(
    west: float, south: float, east: float, north: float,
    *, grid_deg: float = GRID_DEG, max_gentle_keys: int = MAX_GENTLE_KEYS,
) -> tuple[tuple[int, int], list[tuple[int, int]]]:
    """A viewport's bounds -> (hot_key, gentle_keys). hot_key is the grid cell
    containing the viewport's center (the fast-cadence tier); gentle_keys are the
    other grid cells the viewport touches (the slow-cadence tier), nearest-first and
    capped at max_gentle_keys.

    Does not handle a viewport crossing the antimeridian (west > east) -- a known
    simplification for v1, left for a later pass if it turns out to matter in
    practice."""
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0
    hot = _cell(center_lon, center_lat, grid_deg)

    lon_lo, lon_hi = _cell(west, 0.0, grid_deg)[0], _cell(east, 0.0, grid_deg)[0]
    lat_lo, lat_hi = _cell(0.0, south, grid_deg)[1], _cell(0.0, north, grid_deg)[1]

    candidates = [
        (lx, ly)
        for lx in range(lon_lo, lon_hi + 1)
        for ly in range(lat_lo, lat_hi + 1)
        if (lx, ly) != hot
    ]
    candidates.sort(key=lambda c: (c[0] - hot[0]) ** 2 + (c[1] - hot[1]) ** 2)
    return hot, candidates[:max_gentle_keys]


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
    session: aiohttp.ClientSession, lat: float, lon: float, radius_nm: float, *, timeout: float = 10.0,
) -> list[dict] | None:
    """One adsb.lol point+radius query -> its `ac` (aircraft) list, or None on any
    failure (timeout, non-200 -- adsb.lol's free tier 429s far more readily than its
    documented behaviour suggests, malformed response). None is deliberately distinct
    from [] : a failed request must never crash the poll loop, but it also must never
    be reported to callers as "confirmed zero aircraft here" -- see RegionManager.
    record_poll_result(), whose whole reason for accepting None is this distinction."""
    url = f"{ADSB_LOL_BASE}/lat/{lat}/lon/{lon}/dist/{radius_nm}"
    try:
        async with session.get(url, timeout=timeout) as resp:
            if resp.status != 200:
                logger.debug(f"adsb.lol {url} returned {resp.status}")
                return None
            data = await resp.json()
            return data.get("ac", []) or []
    except Exception as exc:
        logger.debug(f"adsb.lol fetch failed for {url}: {exc}")
        return None


class RegionManager:
    """Tracks which region keys are currently subscribed-to and by whom, so a poll
    loop can be shared by every connection watching roughly the same area (docs/adr/
    0009). Takes an explicit `now` on every call rather than reading the wall clock
    itself -- the same tick-driven-state-machine shape this codebase already uses
    (CollectorBase.is_stale(), _refresh.js's onTick()) -- so the grace period is
    testable with controlled timestamps, no real asyncio.sleep/waiting involved.

    Not asyncio-aware itself: owns no tasks, does no I/O. The async polling loop that
    actually queries adsb.lol and pushes results to connections is a thin wrapper
    around this pure state machine, built separately."""

    def __init__(self, *, grace_period_s: float, hot_cadence_s: float = 10.0, gentle_cadence_s: float = 20.0):
        self._grace_period_s = grace_period_s
        self._hot_cadence_s = hot_cadence_s
        self._gentle_cadence_s = gentle_cadence_s
        # region_key -> {connection_id: tier}. A region with an empty dict here is
        # not tracked at all (see unsubscribe -- the dict is popped, not left empty).
        self._subscribers: dict[tuple, dict[str, str]] = {}
        # region_key -> the `now` its last subscriber left. Only present while a
        # region is in its grace period (a fresh subscribe pops the entry).
        self._unsubscribed_at: dict[tuple, float] = {}
        # region_key -> (last_attempted_at, records). Absent entirely if never polled.
        # last_attempted_at advances on every poll attempt, success or failure, so a
        # run of adsb.lol failures still backs off at the region's normal cadence
        # instead of retrying every tick -- see record_poll_result().
        self._last_poll: dict[tuple, tuple[float, list[dict]]] = {}

    def subscribe(self, region_key: tuple, connection_id: str, *, tier: str, now: float) -> None:
        self._subscribers.setdefault(region_key, {})[connection_id] = tier
        self._unsubscribed_at.pop(region_key, None)

    def unsubscribe(self, region_key: tuple, connection_id: str, *, now: float) -> None:
        subs = self._subscribers.get(region_key)
        if subs is None:
            return
        subs.pop(connection_id, None)
        if not subs:
            self._unsubscribed_at[region_key] = now

    def active_regions(self, *, now: float) -> set:
        """Region keys with at least one current subscriber, plus any still within
        their grace period after their last subscriber left."""
        active = {rk for rk, subs in self._subscribers.items() if subs}
        for rk, left_at in self._unsubscribed_at.items():
            if now - left_at < self._grace_period_s:
                active.add(rk)
        return active

    def subscribers_of(self, region_key: tuple) -> set:
        return set(self._subscribers.get(region_key, {}))

    def cadence_for(self, *, rk: tuple) -> float:
        """The hot cadence if ANY current subscriber holds this region as their hot
        key, else the gentle cadence -- a region shared between one connection's hot
        tier and another's gentle tier polls at the faster rate, so everyone benefits
        from the freshest data already being fetched anyway."""
        tiers = self._subscribers.get(rk, {}).values()
        return self._hot_cadence_s if "hot" in tiers else self._gentle_cadence_s

    def regions_due_for_poll(self, *, now: float) -> set:
        due = set()
        for rk in self.active_regions(now=now):
            last = self._last_poll.get(rk)
            if last is None or now - last[0] >= self.cadence_for(rk=rk):
                due.add(rk)
        return due

    def record_poll_result(self, region_key: tuple, records: list[dict] | None, *, now: float) -> None:
        """records=None means the poll attempt failed (adsb.lol non-200/timeout/error)
        -- fetch_aircraft_near's way of distinguishing that from a confirmed-empty [].
        A failure still advances last_attempted_at (so regions_due_for_poll backs off
        at the normal cadence rather than retrying every tick) but keeps whatever
        aircraft were last actually seen there, instead of clobbering them with an
        empty result that was never really "no aircraft", just a rejected request."""
        if records is None:
            previous = self._last_poll.get(region_key)
            records = previous[1] if previous is not None else []
        self._last_poll[region_key] = (now, records)

    def last_result(self, region_key: tuple) -> list[dict]:
        last = self._last_poll.get(region_key)
        return last[1] if last is not None else []
