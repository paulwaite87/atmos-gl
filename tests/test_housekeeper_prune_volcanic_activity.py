#!/usr/bin/env python3
"""Tests for Housekeeper.prune_expired_activity (issue #253), mirroring
test_housekeeper_prune_vessel_tracks.py's wiring-test pattern exactly.
"""
from unittest.mock import patch, MagicMock

from atmos_gl.housekeeper import Housekeeper


def make_bare_housekeeper():
    return Housekeeper.__new__(Housekeeper)


def test_prune_expired_activity_noop_on_falsy_expiry():
    hk = make_bare_housekeeper()
    with patch("atmos_gl.housekeeper.VolcanicActivityAdapter") as MockAdapter:
        hk.prune_expired_activity(0)
    MockAdapter.assert_not_called()


def test_prune_expired_activity_delegates_to_volcanic_activity_adapter():
    hk = make_bare_housekeeper()
    mock_adapter = MagicMock()
    mock_adapter.prune_expired_activity.return_value = 3
    with patch("atmos_gl.housekeeper.VolcanicActivityAdapter", return_value=mock_adapter):
        hk.prune_expired_activity(14)
    mock_adapter.prune_expired_activity.assert_called_once_with(14)


def test_prune_expired_activity_swallows_adapter_errors():
    hk = make_bare_housekeeper()
    mock_adapter = MagicMock()
    mock_adapter.prune_expired_activity.side_effect = RuntimeError("db down")
    with patch("atmos_gl.housekeeper.VolcanicActivityAdapter", return_value=mock_adapter):
        hk.prune_expired_activity(14)  # must not raise
