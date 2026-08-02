#!/usr/bin/env python3
"""Tests for RoundRobinOrder -- the in-memory priority order behind layer_builder's
multi-hour round-robin dispatch. Lets a developer bump a changed layer to the front of
the round without touching a round already in flight (reordering only affects what
_run_dispatch_cycle builds for the NEXT round -- see layer_builder.py's
_run_dispatch_cycle, which asks ordered() fresh each round)."""
import pytest

from atmos_gl.round_robin_order import RoundRobinOrder


def test_ordered_defaults_to_construction_order():
    order = RoundRobinOrder(["isobars", "precipitation", "wind"])

    assert order.ordered({"wind", "isobars", "precipitation"}) == [
        "isobars", "precipitation", "wind",
    ]


def test_ordered_filters_to_only_the_given_pending_set():
    order = RoundRobinOrder(["isobars", "precipitation", "wind"])

    assert order.ordered({"wind", "isobars"}) == ["isobars", "wind"]


def test_reorder_moves_named_sections_to_the_front():
    order = RoundRobinOrder(["isobars", "precipitation", "wind", "currents"])

    order.reorder(["wind"])

    assert order.current() == ["wind", "isobars", "precipitation", "currents"]


def test_reorder_with_multiple_sections_preserves_their_given_order():
    order = RoundRobinOrder(["isobars", "precipitation", "wind", "currents"])

    order.reorder(["currents", "isobars"])

    assert order.current() == ["currents", "isobars", "precipitation", "wind"]


def test_reorder_rejects_an_unknown_section():
    order = RoundRobinOrder(["isobars", "precipitation"])

    with pytest.raises(ValueError, match="bogus"):
        order.reorder(["bogus"])

    # A rejected reorder must not partially apply.
    assert order.current() == ["isobars", "precipitation"]


def test_reset_restores_the_construction_order():
    order = RoundRobinOrder(["isobars", "precipitation", "wind"])
    order.reorder(["wind"])

    order.reset()

    assert order.current() == ["isobars", "precipitation", "wind"]


def test_ordered_reflects_a_reorder_made_since_the_last_call():
    """The whole point: a round already dispatched is untouched (nothing here cancels
    in-flight work), but the NEXT call to ordered() -- i.e. the next round -- picks up
    a reorder made in between."""
    order = RoundRobinOrder(["isobars", "precipitation", "wind"])
    assert order.ordered({"isobars", "precipitation", "wind"}) == [
        "isobars", "precipitation", "wind",
    ]

    order.reorder(["precipitation"])

    assert order.ordered({"isobars", "precipitation", "wind"}) == [
        "precipitation", "isobars", "wind",
    ]
