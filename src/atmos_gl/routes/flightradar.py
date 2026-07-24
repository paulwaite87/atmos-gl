#!/usr/bin/env python3
"""Flight Radar's WebSocket route (issue #203, docs/adr/0009): backend-proxy-and-push
aircraft tracking, region-keyed rather than per-connection, so N browser sessions
never multiply adsb.lol's request load.

The route handler itself only manages subscribe/unsubscribe state (which regions is
this connection currently watching, driven by the viewport messages it sends). The
actual polling -- querying adsb.lol for at most one due region per call (see
RegionManager.next_due_region()), and pushing its result to its current subscribers --
is poll_due_regions(), a standalone coroutine kept independent of any real
WebSocket/network so it's directly testable (see tests/test_flightradar_route.py).
start_background_poller() is the thin, untested-by-design wrapper that calls it on a
fixed tick forever -- same split as LayerBuilder.start_scheduler()'s outer loop vs. its
tested _run_dispatch_cycle().
"""
import asyncio
import itertools
import logging
import time

import aiohttp
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect

from atmos_gl.lib.flight_radar import (
    RegionManager,
    circle_for_region_key,
    fetch_aircraft_near,
    viewport_to_region_keys,
)

logger = logging.getLogger("atmos_gl.routes.flightradar")
router = APIRouter(prefix="/api", tags=["Flight Radar"])

GRACE_PERIOD_S = 30.0
POLL_TICK_S = 1.0

# Hot/gentle cadences are RegionManager's own defaults (10.0s/20.0s) -- not re-declared
# here as separate constants, so there's exactly one place they can drift from.
_region_manager = RegionManager(grace_period_s=GRACE_PERIOD_S)
# connection_id -> the live WebSocket, so poll_due_regions() can push to whichever
# connections are currently subscribed to a region it just polled.
_connections: dict[str, WebSocket] = {}
_connection_ids = itertools.count()
_poller_task: asyncio.Task | None = None


def get_region_manager() -> RegionManager:
    return _region_manager


async def poll_due_regions(region_manager: RegionManager, connections: dict, fetch_fn, *, now: float) -> None:
    """One polling pass: query adsb.lol for AT MOST ONE region -- the longest-waiting
    due region, per RegionManager.next_due_region() -- and push its result to every
    connection subscribed to it. `connections` maps connection_id -> anything with an
    async send_json(dict) method (a real WebSocket in production, a stub in tests).
    `fetch_fn(lat, lon, radius_nm)` is the injected adsb.lol query -- production wires
    fetch_aircraft_near bound to a shared aiohttp.ClientSession; tests inject a fake.
    A failed push to one connection must never stop the others -- that connection is
    very likely mid-disconnect, which its own handler's finally-block will clean up.

    Deliberately not "every due region, each call": adsb.lol's per-IP rate limit (see
    fetch_aircraft_near) tolerates roughly one successful request every 10-12s no
    matter how many different regions are asked about, so firing every due region in
    the same tick just means most of them lose the race to 429 and sit unchanged for a
    full cadence -- confirmed live, 6 of 8 gentle regions around one hot region stayed
    at zero data for minutes. Capping to one call's worth of request per tick, chosen
    by longest-waiting-first, staggers requests to roughly what adsb.lol can actually
    sustain and guarantees every region eventually gets its turn instead of losing to
    whichever region wins a plain iteration-order race every single time.

    fetch_fn returning None means the request failed (adsb.lol rejected/timed out --
    see fetch_aircraft_near) rather than confirming zero aircraft: record_poll_result
    still advances the region's cadence clock so it isn't retried every tick, but there
    is nothing new to tell subscribers, so no message is pushed for it this pass --
    their existing markers (from the last successful poll) are left alone rather than
    being overwritten with a false "no aircraft here"."""
    region_key = region_manager.next_due_region(now=now)
    if region_key is None:
        return
    lat, lon, radius = circle_for_region_key(region_key)
    records = await fetch_fn(lat, lon, radius)
    region_manager.record_poll_result(region_key, records, now=now)
    if records is None:
        return
    message = {"type": "aircraft_update", "region_key": list(region_key), "aircraft": records}
    for conn_id in region_manager.subscribers_of(region_key):
        ws = connections.get(conn_id)
        if ws is None:
            continue
        try:
            await ws.send_json(message)
        except Exception as exc:
            logger.debug(f"push to {conn_id} failed: {exc}")


async def start_background_poller() -> None:
    """Starts the single, app-wide polling loop if it isn't already running. Safe to
    call more than once (e.g. re-entrant startup hooks) -- only the first call does
    anything."""
    global _poller_task
    if _poller_task is not None:
        return

    async def _loop():
        async with aiohttp.ClientSession() as session:
            async def _fetch(lat, lon, radius):
                return await fetch_aircraft_near(session, lat, lon, radius)

            while True:
                try:
                    await poll_due_regions(_region_manager, _connections, _fetch, now=time.monotonic())
                except Exception as exc:
                    logger.error(f"Flight Radar poll loop error: {exc}", exc_info=True)
                await asyncio.sleep(POLL_TICK_S)

    _poller_task = asyncio.create_task(_loop())


@router.websocket("/ws/flightradar")
async def flightradar_ws(
    websocket: WebSocket,
    region_manager: RegionManager = Depends(get_region_manager),
):
    await websocket.accept()
    conn_id = f"conn-{next(_connection_ids)}"
    _connections[conn_id] = websocket
    subscribed: set[tuple] = set()
    try:
        while True:
            msg = await websocket.receive_json()
            if msg.get("type") != "viewport":
                continue
            hot, gentle = viewport_to_region_keys(
                west=msg["west"], south=msg["south"], east=msg["east"], north=msg["north"],
            )
            wanted = {hot} | set(gentle)
            now = time.monotonic()
            for rk in subscribed - wanted:
                region_manager.unsubscribe(rk, conn_id, now=now)
            for rk in wanted:
                region_manager.subscribe(rk, conn_id, tier=("hot" if rk == hot else "gentle"), now=now)
            subscribed = wanted
            await websocket.send_json({
                "type": "subscribed", "hot_key": list(hot), "gentle_keys": [list(g) for g in gentle],
            })
    except WebSocketDisconnect:
        pass
    finally:
        now = time.monotonic()
        for rk in subscribed:
            region_manager.unsubscribe(rk, conn_id, now=now)
        _connections.pop(conn_id, None)
