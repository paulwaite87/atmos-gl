#!/usr/bin/env python3
"""Tests for the lightning frequency/volume redesign: OpenWeather's lightning plan
(60 calls/min, 1,000,000/month) can't sustain "scan every region's full grid every
cycle" -- the old design was ~88,500 requests/sweep across the 12 seeded regions,
~130-250x over budget once the monthly quota is averaged per-second. This covers the
pure grid/queue/rate-limiter pieces that replaced it: build_grid() (wider spacing to
cut overlap), select_points_for_cycle() (viewport-weighted + round-robin budget
selection), RateLimiter (token bucket + shared 429 backoff), and
_points_budget_for_cycle() (which of the two caps actually binds)."""
import time

import pytest

from atmos_gl.collectors.lightning import (
    LightningCollector,
    RateLimiter,
    build_grid,
    select_points_for_cycle,
    MAX_CALLS_PER_MINUTE,
    MAX_CALLS_PER_MONTH,
    SECONDS_PER_MONTH,
)
from atmos_gl.db.lightning_adapter import FakeLightningAdapter


def make_collector(settings=None):
    c = LightningCollector.__new__(LightningCollector)
    c.settings = settings or {}
    return c


# ---- build_grid --------------------------------------------------------------

def test_build_grid_covers_a_single_region_with_correct_point_count():
    regions = [{"label": "A", "lon_min": 0.0, "lat_min": 0.0, "lon_max": 1.0, "lat_max": 1.0}]
    points = build_grid(regions, step_deg=0.5)
    assert len(points) == 4
    lats = sorted(set(p[0] for p in points))
    assert lats == pytest.approx([0.25, 0.75])


def test_build_grid_handles_reversed_min_max():
    """Region dicts aren't guaranteed pre-sorted (lon_min > lon_max is possible
    depending on how the bbox was authored) -- build_grid must not silently produce
    an empty grid for those."""
    regions = [{"label": "A", "lon_min": 1.0, "lat_min": 1.0, "lon_max": 0.0, "lat_max": 0.0}]
    points = build_grid(regions, step_deg=0.5)
    assert len(points) == 4


def test_build_grid_order_is_stable_across_calls():
    """select_points_for_cycle()'s round-robin cursor depends on this: the same
    region set must always flatten to the same point order."""
    regions = [
        {"label": "B", "lon_min": 0.0, "lat_min": 0.0, "lon_max": 1.0, "lat_max": 1.0},
        {"label": "A", "lon_min": 10.0, "lat_min": 10.0, "lon_max": 11.0, "lat_max": 11.0},
    ]
    p1 = build_grid(regions, step_deg=0.5)
    p2 = build_grid(regions, step_deg=0.5)
    assert p1 == p2
    assert p1[0][0] >= 10.0  # "A" sorts before "B" -> its points come first


def test_build_grid_empty_regions_returns_empty():
    assert build_grid([]) == []


# ---- select_points_for_cycle --------------------------------------------------

def test_select_points_round_robins_without_viewport():
    all_points = [(i, i) for i in range(10)]
    selected, cursor = select_points_for_cycle(all_points, cursor=0, budget=4)
    assert selected == all_points[0:4]
    assert cursor == 4

    selected2, cursor2 = select_points_for_cycle(all_points, cursor=cursor, budget=4)
    assert selected2 == all_points[4:8]
    assert cursor2 == 8


def test_select_points_cursor_wraps_around():
    all_points = [(i, i) for i in range(10)]
    selected, cursor = select_points_for_cycle(all_points, cursor=8, budget=4)
    assert selected == [all_points[8], all_points[9], all_points[0], all_points[1]]
    assert cursor == 2


def test_select_points_prioritizes_viewport_nearest_first():
    all_points = [(0, 0), (10, 10), (1, 1), (20, 20)]
    selected, _ = select_points_for_cycle(
        all_points, cursor=0, budget=2, viewport=(0.5, 0.5), viewport_budget_fraction=1.0
    )
    assert set(selected) == {(0, 0), (1, 1)}


def test_select_points_viewport_points_are_not_duplicated_by_round_robin():
    all_points = [(0, 0), (1, 1), (2, 2), (3, 3)]
    selected, _ = select_points_for_cycle(
        all_points, cursor=0, budget=4, viewport=(0, 0), viewport_budget_fraction=0.5
    )
    assert len(selected) == len(set(selected)) == 4


def test_select_points_budget_larger_than_all_points_does_not_hang():
    all_points = [(0, 0), (1, 1)]
    selected, _ = select_points_for_cycle(all_points, cursor=0, budget=100)
    assert selected == all_points


def test_select_points_empty_all_points_returns_empty():
    selected, cursor = select_points_for_cycle([], cursor=5, budget=10)
    assert selected == []
    assert cursor == 5


def test_select_points_zero_budget_returns_empty():
    all_points = [(0, 0), (1, 1)]
    selected, cursor = select_points_for_cycle(all_points, cursor=0, budget=0)
    assert selected == []
    assert cursor == 0


def test_select_points_stale_or_missing_viewport_is_just_none():
    """LightningCollector._current_viewport() returns None for stale/missing
    readings -- select_points_for_cycle must treat that the same as "no viewport"."""
    all_points = [(i, i) for i in range(5)]
    selected, cursor = select_points_for_cycle(all_points, cursor=0, budget=3, viewport=None)
    assert selected == all_points[0:3]
    assert cursor == 3


# ---- _points_budget_for_cycle --------------------------------------------------

def test_points_budget_is_bounded_by_the_monthly_quota_not_the_per_minute_cap():
    """The headline finding of the frequency report this redesign came from: once
    averaged per-second, the monthly quota is far more restrictive than the 60/min
    burst cap, so it must be the one that actually governs the budget."""
    c = make_collector()
    sleep_interval_s = 600.0  # 10 minutes, the slider's default
    expected = int(sleep_interval_s * (MAX_CALLS_PER_MONTH / SECONDS_PER_MONTH))
    per_minute_only = int(sleep_interval_s * (MAX_CALLS_PER_MINUTE / 60.0))

    assert c._points_budget_for_cycle(sleep_interval_s) == expected
    assert expected < per_minute_only


def test_points_budget_scales_with_sleep_interval():
    c = make_collector()
    short = c._points_budget_for_cycle(5 * 60.0)
    long = c._points_budget_for_cycle(30 * 60.0)
    assert long > short


def test_points_budget_is_never_zero():
    c = make_collector()
    assert c._points_budget_for_cycle(0.001) >= 1


# ---- RateLimiter ---------------------------------------------------------------

def test_note_429_sets_an_increasing_backoff():
    limiter = RateLimiter(60)
    t0 = time.monotonic()

    limiter.note_429()
    assert limiter._consecutive_429s == 1
    assert limiter._backoff_until >= t0 + 2 - 0.05  # ~2^1s

    limiter.note_429()
    assert limiter._consecutive_429s == 2
    assert limiter._backoff_until >= t0 + 4 - 0.05  # ~2^2s


def test_note_429_backoff_caps_at_60s():
    limiter = RateLimiter(60)
    limiter._consecutive_429s = 10  # simulate many prior consecutive failures
    t0 = time.monotonic()
    limiter.note_429()
    assert limiter._backoff_until <= t0 + 60 + 0.05


def test_note_success_resets_consecutive_429_count():
    limiter = RateLimiter(60)
    limiter.note_429()
    limiter.note_429()
    assert limiter._consecutive_429s == 2
    limiter.note_success()
    assert limiter._consecutive_429s == 0


@pytest.mark.asyncio
async def test_acquire_does_not_block_while_tokens_are_available():
    limiter = RateLimiter(calls_per_minute=6000)  # plenty of headroom for a fast test
    start = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - start < 0.05
    assert limiter.tokens == pytest.approx(5999.0, abs=0.5)


@pytest.mark.asyncio
async def test_acquire_waits_out_an_active_backoff():
    limiter = RateLimiter(calls_per_minute=6000)  # tokens plentiful; only backoff should gate
    limiter._backoff_until = time.monotonic() + 0.05
    start = time.monotonic()
    await limiter.acquire()
    assert time.monotonic() - start >= 0.04


# ---- fetch_and_store: rate limiter + backoff integration -----------------------

class _FakeResponse:
    def __init__(self, status, json_body=None):
        self.status = status
        self._json_body = json_body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def json(self):
        return self._json_body


class _FakeSession:
    def __init__(self, response):
        self._response = response
        self.get_calls = 0

    def get(self, url, params=None, timeout=None):
        self.get_calls += 1
        return self._response


def make_fetch_collector():
    c = LightningCollector.__new__(LightningCollector)
    c.settings = {}
    c.api_key = "test-key"
    c.url = "https://example.test/lightning"
    c.lightning_adapter = FakeLightningAdapter()
    c.rate_limiter = RateLimiter(calls_per_minute=6000)
    return c


@pytest.mark.asyncio
async def test_fetch_and_store_skips_the_request_entirely_without_an_api_key():
    c = make_fetch_collector()
    c.api_key = None
    session = _FakeSession(_FakeResponse(200, {"lightnings": []}))

    n = await c.fetch_and_store(session, 0.0, 0.0, "2026-01-01T00:00:00Z", "2026-01-01T00:20:00Z")

    assert n == 0
    assert session.get_calls == 0


@pytest.mark.asyncio
async def test_fetch_and_store_stores_strikes_and_notes_success_on_200():
    c = make_fetch_collector()
    body = {
        "lightnings": [
            {"id": "s1", "lat": -41.0, "lon": 174.0, "quality": 90, "datetime": "2026-01-01T00:10:00Z"}
        ]
    }
    session = _FakeSession(_FakeResponse(200, body))

    n = await c.fetch_and_store(session, -41.0, 174.0, "2026-01-01T00:00:00Z", "2026-01-01T00:20:00Z")

    assert n == 1
    assert "s1" in c.lightning_adapter._strikes
    assert c.rate_limiter._consecutive_429s == 0


@pytest.mark.asyncio
async def test_fetch_and_store_notes_429_and_stores_nothing():
    c = make_fetch_collector()
    session = _FakeSession(_FakeResponse(429))

    n = await c.fetch_and_store(session, 0.0, 0.0, "2026-01-01T00:00:00Z", "2026-01-01T00:20:00Z")

    assert n == 0
    assert c.lightning_adapter._strikes == {}
    assert c.rate_limiter._consecutive_429s == 1
    assert c.rate_limiter._backoff_until > time.monotonic()
