#!/usr/bin/env python3
"""The Country Statistics catalog's integrity: a bad entry should fail here, not on the
live map."""
from collections import Counter

from terrascope.lib.country_stats import GROUPS, INDICATORS, catalog

_COLORMAPS = {"viridis", "magma", "inferno", "ylorrd", "coolwarm", "turbo"}  # country_stats.js's


def test_indicator_ids_are_unique():
    ids = [i.id for i in INDICATORS]
    assert len(ids) == len(set(ids))


def test_every_entry_has_a_known_group_scale_and_colormap():
    groups = {g for g, _ in GROUPS}
    for i in INDICATORS:
        assert i.group in groups, i.id
        assert i.scale in ("linear", "log"), i.id
        assert i.colormap in _COLORMAPS, i.id


def test_the_three_groups_hold_41_indicators():
    assert Counter(i.group for i in INDICATORS) == {"people": 14, "economy": 13, "environment": 14}


def test_diverging_indicators_are_linear():
    # A log scale can't straddle zero.
    assert all(i.scale == "linear" for i in INDICATORS if i.diverging)


def test_only_survey_based_indicators_override_the_cutoff():
    assert {i.id for i in INDICATORS if i.max_age_years is not None} == {"extreme_poverty", "gini"}


def test_the_catalog_lists_groups_and_indicators_in_order():
    groups = catalog()
    assert [g["label"] for g in groups] == ["People & Health", "Economy & Society", "Energy & Environment"]
    assert groups[0]["indicators"][0] == {"id": "population", "label": "Population"}
    assert [i["id"] for g in groups for i in g["indicators"]] == [i.id for i in INDICATORS]
