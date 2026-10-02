#!/usr/bin/env python3
"""Tests for Housekeeper.prune_expired_world_events, mirroring
test_housekeeper_prune_volcanic_activity.py's wiring-test pattern. The adapter-level
behaviour (delete_expired, prune_orphaned_articles) is covered by
test_world_event_adapter_real_vs_fake.py."""
from unittest.mock import MagicMock, patch

from atmos_gl.housekeeper import Housekeeper


def make_bare_housekeeper():
    return Housekeeper.__new__(Housekeeper)


def test_prune_expired_world_events_noop_on_falsy_expiry():
    hk = make_bare_housekeeper()
    with patch("atmos_gl.housekeeper.WorldEventAdapter") as MockAdapter:
        hk.prune_expired_world_events(0)
    MockAdapter.assert_not_called()


def test_prune_expired_world_events_prunes_events_then_orphaned_articles():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.delete_expired.return_value = 5
    adapter.prune_orphaned_articles.return_value = 2
    with patch("atmos_gl.housekeeper.WorldEventAdapter", return_value=adapter):
        hk.prune_expired_world_events(14)
    adapter.delete_expired.assert_called_once_with(14)
    adapter.prune_orphaned_articles.assert_called_once_with()
    # events first: articles only become orphaned once their events are gone
    assert [c[0] for c in adapter.method_calls] == ["delete_expired", "prune_orphaned_articles"]


def test_prune_expired_world_events_swallows_adapter_errors():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.delete_expired.side_effect = RuntimeError("db down")
    with patch("atmos_gl.housekeeper.WorldEventAdapter", return_value=adapter):
        hk.prune_expired_world_events(14)  # must not raise
