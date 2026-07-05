#!/usr/bin/env python3
"""Tests for Housekeeper.prune_vessel_tracks (issue #30: ShipAdapter.prune_vessel_tracks
had no caller anywhere, and shipping_collector.vessel_track_expiry_days sat unused in
config). Wires the two together via the same expiry-gated pattern prune_fields already
uses.
"""
from unittest.mock import patch, MagicMock

from worldmap.housekeeper import Housekeeper


def make_bare_housekeeper():
    return Housekeeper.__new__(Housekeeper)


def test_prune_vessel_tracks_noop_on_falsy_expiry():
    hk = make_bare_housekeeper()
    with patch("worldmap.housekeeper.ShipAdapter") as MockAdapter:
        hk.prune_vessel_tracks(0)
    MockAdapter.assert_not_called()


def test_prune_vessel_tracks_delegates_to_ship_adapter():
    hk = make_bare_housekeeper()
    mock_adapter = MagicMock()
    mock_adapter.prune_vessel_tracks.return_value = 5
    with patch("worldmap.housekeeper.ShipAdapter", return_value=mock_adapter):
        hk.prune_vessel_tracks(14)
    mock_adapter.prune_vessel_tracks.assert_called_once_with(14)


def test_prune_vessel_tracks_swallows_adapter_errors():
    hk = make_bare_housekeeper()
    mock_adapter = MagicMock()
    mock_adapter.prune_vessel_tracks.side_effect = RuntimeError("db down")
    with patch("worldmap.housekeeper.ShipAdapter", return_value=mock_adapter):
        hk.prune_vessel_tracks(14)  # must not raise
