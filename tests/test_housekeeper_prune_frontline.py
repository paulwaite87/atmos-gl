#!/usr/bin/env python3
"""Tests for Housekeeper.prune_frontline_snapshots, mirroring
test_housekeeper_prune_world_events.py's wiring-test pattern. The adapter-level
behaviour (prune_older_than) is covered by test_frontline_adapter_real_vs_fake.py."""
from unittest.mock import MagicMock, patch

from atmos_gl.housekeeper import Housekeeper


def make_bare_housekeeper():
    return Housekeeper.__new__(Housekeeper)


def test_prune_frontline_noop_on_falsy_expiry():
    hk = make_bare_housekeeper()
    with patch("atmos_gl.housekeeper.FrontlineAdapter") as MockAdapter:
        hk.prune_frontline_snapshots(0)
    MockAdapter.assert_not_called()


def test_prune_frontline_passes_the_expiry_through():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.prune_older_than.return_value = 3
    with patch("atmos_gl.housekeeper.FrontlineAdapter", return_value=adapter):
        hk.prune_frontline_snapshots(60)
    adapter.prune_older_than.assert_called_once_with(60)


def test_prune_frontline_never_cuts_inside_the_longest_gains_losses_window():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    with patch("atmos_gl.housekeeper.FrontlineAdapter", return_value=adapter):
        hk.prune_frontline_snapshots(5)
    adapter.prune_older_than.assert_called_once_with(30)


def test_prune_frontline_swallows_adapter_errors():
    hk = make_bare_housekeeper()
    adapter = MagicMock()
    adapter.prune_older_than.side_effect = RuntimeError("db down")
    with patch("atmos_gl.housekeeper.FrontlineAdapter", return_value=adapter):
        hk.prune_frontline_snapshots(60)  # must not raise
