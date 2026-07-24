#!/usr/bin/env python3
"""Tests for lib/flight_radar.py -- the pure geometry helpers and the
RegionManager subscription lifecycle behind the Flight Radar layer (issue #203,
docs/adr/0009). No real network, no real asyncio.sleep/wall-clock waiting in any of
these -- RegionManager takes an injectable clock so its 30s grace-period behavior is
testable without actually waiting."""
import pytest

from atmos_gl.lib.flight_radar import (
    viewport_to_region_keys,
    circle_for_region_key,
    RegionManager,
)


def test_viewport_to_region_keys_single_cell_viewport_has_no_gentle_keys():
    """A viewport entirely within one grid cell needs only the hot circle."""
    hot, gentle = viewport_to_region_keys(west=0.1, south=0.1, east=0.2, north=0.2, grid_deg=5.0)
    assert hot == (0, 0)
    assert gentle == []


def test_viewport_to_region_keys_hot_key_is_the_cell_containing_the_center():
    hot, _gentle = viewport_to_region_keys(west=-2.0, south=-2.0, east=8.0, north=8.0, grid_deg=5.0)
    assert hot == (0, 0)  # center (3.0, 3.0) -> floor(3/5) on both axes


def test_viewport_to_region_keys_gentle_keys_cover_the_rest_excluding_hot():
    hot, gentle = viewport_to_region_keys(west=-2.0, south=-2.0, east=8.0, north=8.0, grid_deg=5.0)
    assert hot not in gentle
    assert len(gentle) > 0


def test_viewport_to_region_keys_caps_gentle_keys_at_max():
    hot, gentle = viewport_to_region_keys(
        west=-50, south=-50, east=50, north=50, grid_deg=5.0, max_gentle_keys=6
    )
    assert hot not in gentle
    assert len(gentle) == 6


def test_viewport_to_region_keys_prioritizes_gentle_keys_closest_to_hot():
    """A wide, zoomed-out viewport touches far more cells than the cap allows -- the
    ones actually kept must be the cells nearest the hot cell, not arbitrary ones."""
    hot, gentle = viewport_to_region_keys(
        west=-50, south=-50, east=50, north=50, grid_deg=5.0, max_gentle_keys=4
    )
    assert all(abs(gx - hot[0]) <= 2 and abs(gy - hot[1]) <= 2 for gx, gy in gentle)


def test_circle_for_region_key_centers_on_the_cell_center():
    lat, lon, _radius = circle_for_region_key((0, 0), grid_deg=5.0)
    assert lon == pytest.approx(2.5)
    assert lat == pytest.approx(2.5)


def test_circle_for_region_key_handles_negative_cells():
    lat, lon, _radius = circle_for_region_key((-1, -1), grid_deg=5.0)
    assert lon == pytest.approx(-2.5)
    assert lat == pytest.approx(-2.5)


def test_circle_for_region_key_uses_the_configured_radius():
    _lat, _lon, radius = circle_for_region_key((0, 0), grid_deg=5.0, radius_nm=123.0)
    assert radius == 123.0


# ---- RegionManager: subscription lifecycle + grace period -----------------------
# Takes an explicit `now` on every call rather than reading the wall clock or calling
# asyncio.sleep itself -- same tick-driven-state-machine shape this codebase already
# uses (CollectorBase.is_stale()/interval_elapsed(), _refresh.js's onTick()), so the
# 30s grace period is testable by just passing controlled timestamps, no real waiting.

def test_region_manager_marks_a_region_active_on_first_subscriber():
    rm = RegionManager(grace_period_s=30.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    assert (0, 0) in rm.active_regions(now=0.0)


def test_region_manager_stays_active_immediately_after_last_unsubscribe():
    rm = RegionManager(grace_period_s=30.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.unsubscribe((0, 0), "conn-1", now=10.0)
    assert (0, 0) in rm.active_regions(now=10.0)  # grace period hasn't elapsed yet


def test_region_manager_becomes_inactive_after_the_grace_period_elapses():
    rm = RegionManager(grace_period_s=30.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.unsubscribe((0, 0), "conn-1", now=10.0)
    assert (0, 0) not in rm.active_regions(now=10.0 + 30.0 + 0.001)


def test_region_manager_cancels_pending_expiry_on_a_new_subscriber():
    """A new subscriber arriving during the grace period keeps the region alive
    indefinitely -- the earlier unsubscribe's countdown must not still fire later."""
    rm = RegionManager(grace_period_s=30.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.unsubscribe((0, 0), "conn-1", now=10.0)
    rm.subscribe((0, 0), "conn-2", tier="gentle", now=15.0)  # within the grace period
    assert (0, 0) in rm.active_regions(now=10.0 + 30.0 + 0.001)


def test_region_manager_shares_one_region_across_multiple_subscribers():
    """Only removed from active tracking once EVERY subscriber has left."""
    rm = RegionManager(grace_period_s=30.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.subscribe((0, 0), "conn-2", tier="gentle", now=1.0)
    rm.unsubscribe((0, 0), "conn-1", now=5.0)
    assert (0, 0) in rm.active_regions(now=5.0 + 30.0 + 0.001)  # conn-2 still there


def test_region_manager_unsubscribe_of_an_unknown_connection_is_a_no_op():
    rm = RegionManager(grace_period_s=30.0)
    rm.unsubscribe((9, 9), "never-subscribed", now=0.0)
    assert (9, 9) not in rm.active_regions(now=0.0)


# ---- RegionManager: cadence tiers + poll scheduling ------------------------------

def test_region_manager_uses_the_hot_cadence_when_any_subscriber_is_hot():
    """A region that's one connection's gentle-tier key but another's hot-tier key
    gets the fast cadence -- everyone benefits from the freshest data available."""
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="gentle", now=0.0)
    rm.subscribe((0, 0), "conn-2", tier="hot", now=0.0)
    assert rm.cadence_for(rk=(0, 0)) == 2.0


def test_region_manager_uses_the_gentle_cadence_when_no_subscriber_is_hot():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="gentle", now=0.0)
    assert rm.cadence_for(rk=(0, 0)) == 20.0


def test_region_manager_a_never_polled_active_region_is_due():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    assert (0, 0) in rm.regions_due_for_poll(now=0.0)


def test_region_manager_a_recently_polled_region_is_not_due():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.record_poll_result((0, 0), [{"hex": "a1"}], now=0.0)
    assert (0, 0) not in rm.regions_due_for_poll(now=1.0)  # cadence is 2.0s


def test_region_manager_a_region_becomes_due_again_after_its_cadence_elapses():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.record_poll_result((0, 0), [{"hex": "a1"}], now=0.0)
    assert (0, 0) in rm.regions_due_for_poll(now=2.001)


def test_region_manager_an_inactive_region_is_never_due():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    assert (0, 0) not in rm.regions_due_for_poll(now=0.0)


def test_region_manager_last_result_returns_the_most_recently_recorded_records():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.record_poll_result((0, 0), [{"hex": "a1"}], now=0.0)
    assert rm.last_result((0, 0)) == [{"hex": "a1"}]


def test_region_manager_last_result_for_a_never_polled_region_is_empty():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    assert rm.last_result((0, 0)) == []


def test_region_manager_subscribers_of_returns_current_connection_ids():
    rm = RegionManager(grace_period_s=30.0, hot_cadence_s=2.0, gentle_cadence_s=20.0)
    rm.subscribe((0, 0), "conn-1", tier="hot", now=0.0)
    rm.subscribe((0, 0), "conn-2", tier="gentle", now=0.0)
    assert rm.subscribers_of((0, 0)) == {"conn-1", "conn-2"}
