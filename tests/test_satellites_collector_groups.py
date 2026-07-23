#!/usr/bin/env python3
"""Tests for SatellitesCollector._groups() -- the config UI's grouped_transfer shuttle
control (field_specs.py's _CELESTRAK_GROUPS) saves an array, like every other
MultiSelectSpec-backed field, replacing the old comma-separated string.
"""
from atmos_gl.collectors.satellites import SatellitesCollector


def make_collector(settings=None):
    c = SatellitesCollector.__new__(SatellitesCollector)
    c.settings = settings or {}
    return c


def test_groups_returns_the_configured_list():
    c = make_collector(settings={"groups": ["starlink", "stations"]})
    assert c._groups() == ["starlink", "stations"]


def test_groups_defaults_when_unset():
    c = make_collector(settings={})
    assert c._groups() == ["stations", "weather", "science", "resource"]


def test_groups_returns_empty_list_when_explicitly_empty():
    c = make_collector(settings={"groups": []})
    assert c._groups() == []
