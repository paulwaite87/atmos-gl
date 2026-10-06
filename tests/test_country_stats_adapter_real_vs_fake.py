#!/usr/bin/env python3
"""Guard against CountryStatsAdapter Real/Fake drift: the fake hand-reimplements the
replace-wholesale save and the metadata round trip independently."""
import contextlib
from unittest.mock import patch

import pytest
from sqlalchemy.orm import sessionmaker

from atmos_gl.db.country_stats_adapter import CountryStatsAdapter, FakeCountryStatsAdapter


def _make_adapter(kind, real_db):
    if kind == "real":
        TestSession = sessionmaker(bind=real_db)
        return CountryStatsAdapter(), patch("atmos_gl.db.country_stats_adapter.Session", TestSession)
    return FakeCountryStatsAdapter(), contextlib.nullcontext()


_METADATA = {"title": "Population", "unit": "people", "short_unit": None,
             "citation": "UN WPP (2024)", "source_last_updated": "2024-07-15",
             "next_update": "2025-07-15"}


# The real tables are shared across tests, so each test uses its own indicator ids.
@pytest.mark.parametrize("kind", ["real", "fake"])
def test_saved_indicator_round_trips(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_indicator("t_round_trip", _METADATA, {"FRA": (2023, 66438826.0), "KOS": (2022, 1.5)})
        stored = adapter.get_indicator("t_round_trip")
    assert stored == {"metadata": _METADATA,
                      "rows": {"FRA": (2023, 66438826.0), "KOS": (2022, 1.5)}}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_saving_again_replaces_every_value(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_indicator("t_replace", _METADATA, {"FRA": (2022, 1.0), "NOR": (2022, 2.0)})
        adapter.save_indicator("t_replace", {**_METADATA, "source_last_updated": "2025-07-15"},
                               {"FRA": (2023, 3.0)})
        stored = adapter.get_indicator("t_replace")
        last_updated = adapter.get_source_last_updated("t_replace")
    assert stored["rows"] == {"FRA": (2023, 3.0)}
    assert last_updated == "2025-07-15"


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_indicators_are_independent(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        adapter.save_indicator("t_a", _METADATA, {"FRA": (2023, 1.0)})
        adapter.save_indicator("t_b", _METADATA, {"NOR": (2023, 2.0)})
        assert adapter.get_indicator("t_a")["rows"] == {"FRA": (2023, 1.0)}


@pytest.mark.parametrize("kind", ["real", "fake"])
def test_an_unstored_indicator_is_none(kind, real_db):
    adapter, ctx = _make_adapter(kind, real_db)
    with ctx:
        assert adapter.get_indicator("t_never") is None
        assert adapter.get_source_last_updated("t_never") is None
