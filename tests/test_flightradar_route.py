#!/usr/bin/env python3
"""Tests for routes/flightradar.py (issue #203, docs/adr/0009).

poll_due_regions() is tested directly (no real WebSocket, no real network) with a
plain dict of connection-id -> fake-connection stubs and an injected fetch_fn. The
route's viewport-subscribe/unsubscribe handling is tested via FastAPI's
TestClient.websocket_connect, with get_region_manager overridden to a fresh
RegionManager per test -- mirroring the DI-seam pattern routes/status.py already uses
for its collector-class registries."""
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from atmos_gl.api import app
from atmos_gl.lib.flight_radar import RegionManager
from atmos_gl.routes.flightradar import get_region_manager, poll_due_regions


# ---- poll_due_regions --------------------------------------------------------

@pytest.mark.asyncio
async def test_poll_due_regions_pushes_to_all_subscribers_of_a_polled_region():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.subscribe((0, 0), "conn-2", tier="gentle", now=0.0)
    ws1, ws2 = AsyncMock(), AsyncMock()
    connections = {"conn-1": ws1, "conn-2": ws2}
    fetch_fn = AsyncMock(return_value=[{"hex": "a1"}])

    await poll_due_regions(rm, connections, fetch_fn, now=0.0)

    ws1.send_json.assert_awaited_once()
    ws2.send_json.assert_awaited_once()
    assert ws1.send_json.call_args.args[0]["aircraft"] == [{"hex": "a1"}]


@pytest.mark.asyncio
async def test_poll_due_regions_skips_regions_not_due():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.record_poll_result((0, 0), [{"hex": "a1"}], now=0.0)
    connections = {"conn-1": AsyncMock()}
    fetch_fn = AsyncMock()

    await poll_due_regions(rm, connections, fetch_fn, now=1.0)  # cadence is 2.0s

    fetch_fn.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_due_regions_a_send_failure_does_not_stop_other_pushes():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.subscribe((0, 0), "conn-2", tier="hot", now=0.0)
    ws1 = AsyncMock()
    ws1.send_json.side_effect = RuntimeError("connection closing")
    ws2 = AsyncMock()
    connections = {"conn-1": ws1, "conn-2": ws2}
    fetch_fn = AsyncMock(return_value=[])

    await poll_due_regions(rm, connections, fetch_fn, now=0.0)

    ws2.send_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_poll_due_regions_records_the_result_so_it_is_not_due_again_immediately():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    fetch_fn = AsyncMock(return_value=[{"hex": "a1"}])

    await poll_due_regions(rm, {"conn-1": AsyncMock()}, fetch_fn, now=0.0)

    assert (0, 0) not in rm.regions_due_for_poll(now=0.5)
    assert rm.last_result((0, 0)) == [{"hex": "a1"}]


# ---- WebSocket route: viewport subscribe/unsubscribe -------------------------

def _wait_until(predicate, *, timeout=1.0, interval=0.01):
    """TestClient's websocket_connect runs the server in a background thread -- its
    __exit__ returning doesn't guarantee the server-side disconnect handler's finally
    block has already run. Bounded poll instead of a fixed sleep, so this is fast on
    the happy path and fails outright (not silently flaky) if the condition never
    becomes true within timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(interval)
    assert predicate(), "condition never became true within timeout"


@pytest.fixture
def region_manager():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    app.dependency_overrides[get_region_manager] = lambda: rm
    yield rm
    app.dependency_overrides.pop(get_region_manager, None)


def test_flightradar_ws_subscribes_to_region_keys_from_the_viewport_message(region_manager):
    client = TestClient(app)
    with client.websocket_connect("/api/ws/flightradar") as ws:
        ws.send_json({"type": "viewport", "west": 0.1, "south": 0.1, "east": 0.2, "north": 0.2})
        ack = ws.receive_json()  # synchronizes with the server having processed it

    assert ack["type"] == "subscribed"
    # The route timestamps subscribe/unsubscribe with the real time.monotonic() (not
    # an injected clock, unlike RegionManager's own unit tests) -- query with the same
    # clock rather than a literal 0.0, or a grace-period comparison against a huge
    # real monotonic value would look bogus.
    assert (0, 0) in region_manager.active_regions(now=time.monotonic())


def test_flightradar_ws_moving_the_viewport_unsubscribes_the_old_region(region_manager):
    client = TestClient(app)
    with client.websocket_connect("/api/ws/flightradar") as ws:
        ws.send_json({"type": "viewport", "west": 0.1, "south": 0.1, "east": 0.2, "north": 0.2})
        ws.receive_json()
        ws.send_json({"type": "viewport", "west": 30.1, "south": 30.1, "east": 30.2, "north": 30.2})
        ack = ws.receive_json()  # synchronizes with the server having processed the second viewport

    assert ack["hot_key"] == [6, 6]
    assert (6, 6) in region_manager.active_regions(now=time.monotonic())
    # (0, 0) can still report as "active" for up to its 30s grace period after its last
    # subscriber leaves -- that's RegionManager's designed behavior (see
    # tests/test_lib_flight_radar.py's grace-period tests), not a bug. Check that this
    # connection was actually removed from (0, 0)'s subscriber set instead.
    _wait_until(lambda: region_manager.subscribers_of((0, 0)) == set())


def test_flightradar_ws_disconnect_unsubscribes_from_every_region(region_manager):
    client = TestClient(app)
    with client.websocket_connect("/api/ws/flightradar") as ws:
        ws.send_json({"type": "viewport", "west": 0.1, "south": 0.1, "east": 10.0, "north": 10.0})
        ack = ws.receive_json()
        regions = [tuple(ack["hot_key"])] + [tuple(g) for g in ack["gentle_keys"]]
        assert all(region_manager.subscribers_of(rk) for rk in regions)  # non-empty while connected

    # After the `with` block exits, the client has disconnected -- but the server-side
    # handler's finally-block cleanup runs in a background thread and isn't guaranteed
    # to have completed by the time __exit__ returns control here, so poll rather than
    # assert once. Regions may still report as "active" for up to their 30s grace
    # period (by design -- see the comment above), so check each region's subscriber
    # set directly rather than active_regions().
    _wait_until(lambda: all(not region_manager.subscribers_of(rk) for rk in regions))
